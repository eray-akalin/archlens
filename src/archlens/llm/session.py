"""Tool-calling session loop on top of `LLMClientProtocol.complete` (docs/LLM.md §4-5).

While the model asks for tools, run them through the `ToolSession` (which logs, marks the ledger
and wraps results) and continue; stop at the first parsed answer. When the tool-call or context
budget is reached, one final turn without tools asks for the answer. An answer that fails the
schema is retried once with the error appended. The loop never raises for model-side problems:
the outcome carries `error` ("invalid_output", "budget", "llm_error") instead, so the caller can
turn them into `unknown` verdicts. Only a cassette miss (a broken test) propagates.

Tool calls run one at a time, in the order the model gave them, so search logs and the ledger
are reproducible.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from archlens.config import SessionLimits
from archlens.errors import BudgetExceeded, CassetteMiss, InvalidModelOutput, LLMError
from archlens.llm.client import LLMClientProtocol
from archlens.llm.cost import estimate_messages
from archlens.llm.types import ChatMessage, LLMRequest
from archlens.models import LLMCallRecord
from archlens.tools.repo_tools import ToolSession

BUDGET_EXHAUSTED = "Tool budget exhausted; answer now with what you have."
TOOL_REFUSED = "error: tool budget exhausted; no more tool calls in this session"
INVALID_ANSWER = (
    "Your previous answer could not be used ({error}). Answer again with exactly the requested "
    "structure."
)
NO_TOOLS_LEFT = "No tools are available now. Answer with the requested structure."

SessionError = Literal["invalid_output", "budget", "llm_error"]


@dataclass(frozen=True)
class SessionOutcome[T: BaseModel]:
    parsed: T | None
    error: SessionError | None  # set iff parsed is None
    detail: str | None
    records: tuple[LLMCallRecord, ...]
    tool_calls: int
    messages: tuple[ChatMessage, ...]


async def run_tool_session[T: BaseModel](
    llm: LLMClientProtocol,
    *,
    role: str,
    stage: str,
    prompt_version: str,
    messages: Sequence[ChatMessage],
    response_format: type[T],
    tools: ToolSession,
    limits: SessionLimits,
    metric: str | None = None,
    tags: tuple[str, ...] = (),
) -> SessionOutcome[T]:
    """Run one session to a parsed `response_format` answer, or an outcome with `error`."""
    history: list[ChatMessage] = list(messages)
    records: list[LLMCallRecord] = []
    calls_used = 0
    retried = False
    final = False

    def outcome(
        parsed: T | None, error: SessionError | None = None, detail: str | None = None
    ) -> SessionOutcome[T]:
        return SessionOutcome(parsed, error, detail, tuple(records), calls_used, tuple(history))

    while True:
        over_budget = calls_used >= limits.max_tool_calls
        if not final and (over_budget or estimate_messages(history) >= limits.max_context_tokens):
            final = True
            history.append({"role": "user", "content": BUDGET_EXHAUSTED})
        request = LLMRequest(
            role=role,
            stage=stage,
            prompt_version=prompt_version,
            messages=list(history),
            response_format=response_format,
            metric=metric,
            tools=() if final else tools.specs(),
            tags=tags,
        )
        try:
            result = await llm.complete(request)
        except InvalidModelOutput as exc:
            records.extend(exc.records)
            if retried:
                return outcome(None, "invalid_output", str(exc)[:500])
            retried = True
            if exc.content:
                history.append({"role": "assistant", "content": exc.content})
            history.append({"role": "user", "content": INVALID_ANSWER.format(error=str(exc)[:500])})
            continue
        except BudgetExceeded as exc:
            return outcome(None, "budget", str(exc)[:500])
        except CassetteMiss:
            raise
        except LLMError as exc:
            return outcome(None, "llm_error", f"{type(exc).__name__}: {exc}"[:500])
        records.extend(result.records)
        if result.parsed is not None:
            return outcome(result.parsed)
        if final:  # asked for tools although none were offered
            if retried:
                return outcome(None, "invalid_output", "requested tools after the budget ran out")
            retried = True
            history.append({"role": "user", "content": NO_TOOLS_LEFT})
            continue
        history.append(result.message)
        for call in result.tool_calls:
            if calls_used < limits.max_tool_calls:
                content = await tools.call(call.name, call.arguments)
            else:
                content = TOOL_REFUSED
            calls_used += 1
            history.append({"role": "tool", "tool_call_id": call.id, "content": content})
