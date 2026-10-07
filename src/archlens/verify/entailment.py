"""Step 2 — entailment (docs/LLM.md §6): a cheap model judges whether cited snippets support the
claim. Up to 5 findings of one metric per call; output `EntailmentBatchOutput`, one item per ref.

The claim and the snippets go in wrapped (`untrusted.wrap`): a claim can carry text the evaluator
copied from the repository. An unusable answer is retried once; after that, or on a provider
error, the batch's items come back with `error` set (→ `unverified`). `BudgetExceeded`
propagates so the pipeline can stop every remaining LLM step.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from archlens.errors import BudgetExceeded, CassetteMiss, InvalidModelOutput, LLMError
from archlens.llm.client import LLMClientProtocol
from archlens.llm.prompts import DEFAULT_PROMPTS_DIR, Prompt, joined_version, load_prompt
from archlens.llm.types import ChatMessage, LLMRequest
from archlens.llm.untrusted import wrap
from archlens.models import CheckResult, CodeEvidence, EntailmentBatchOutput, LLMCallRecord

BATCH_SIZE = 5
MAX_RATIONALE_CHARS = 200
STAGE = "verify"
RETRY = "Your previous answer could not be used ({error}). Answer again with one item per ref."

Supports = Literal["yes", "no", "insufficient"]


@dataclass(frozen=True)
class EntailmentPrompts:
    system: Prompt
    findings: Prompt

    @classmethod
    def load(cls, directory: Path = DEFAULT_PROMPTS_DIR) -> "EntailmentPrompts":
        return cls(
            load_prompt(directory / "verifier.entailment.md"),
            load_prompt(directory / "verifier.findings.md"),
        )

    @classmethod
    def load_absence(cls, directory: Path = DEFAULT_PROMPTS_DIR) -> "EntailmentPrompts":
        """The absence judge: same output, but the code is what the absence probes found."""
        return cls(
            load_prompt(directory / "verifier.absence.md"),
            load_prompt(directory / "verifier.probe_hits.md"),
        )

    @property
    def version(self) -> str:
        return joined_version(self.system, self.findings)


@dataclass(frozen=True)
class EntailmentInput:
    ref: str  # unique within the batch (the check id)
    title: str
    result: CheckResult
    evidence: Sequence[CodeEvidence]


@dataclass(frozen=True)
class EntailmentAnswer:
    supports: Supports | None  # None when the batch failed or the item is missing
    rationale: str
    llm_call_id: str | None


def batches(items: Sequence[EntailmentInput]) -> list[list[EntailmentInput]]:
    return [list(items[i : i + BATCH_SIZE]) for i in range(0, len(items), BATCH_SIZE)]


def render_code(evidence: Sequence[CodeEvidence], boundary: str) -> str:
    """Each snippet line-numbered (`  34│ code`) and wrapped with its path as source."""
    blocks: list[str] = []
    for item in evidence:
        rows = item.snippet.split("\n")
        width = max(4, len(str(item.start_line + len(rows) - 1)))
        body = "\n".join(f"{item.start_line + i:>{width}}│ {row}" for i, row in enumerate(rows))
        blocks.append(wrap(body, item.path, boundary))
    return "\n".join(blocks)


async def entail_batch(
    llm: LLMClientProtocol,
    prompts: EntailmentPrompts,
    items: Sequence[EntailmentInput],
    *,
    boundary: str,
    metric: str | None,
) -> dict[str, EntailmentAnswer]:
    """One answer per ref of `items` (≤ 5). Raises BudgetExceeded; never raises for other model
    problems (answers then carry `supports=None` and the reason)."""
    refs = [item.ref for item in items]
    findings = [
        {
            "ref": item.ref,
            "title": item.title,
            "verdict": item.result.verdict,
            "claim": wrap(item.result.claim, f"claim {item.ref}", boundary),
            "code": render_code(item.evidence, boundary),
        }
        for item in items
    ]
    messages: list[ChatMessage] = [
        {"role": "system", "content": prompts.system.render()},
        {"role": "user", "content": prompts.findings.render(findings=findings, refs=refs)},
    ]
    records: list[LLMCallRecord] = []
    error = "no answer"
    for attempt in range(2):
        request = LLMRequest(
            role="verifier",
            stage=STAGE,
            prompt_version=prompts.version,
            messages=list(messages),
            response_format=EntailmentBatchOutput,
            metric=metric,
            tags=tuple(refs),
        )
        try:
            result = await llm.complete(request)
        except BudgetExceeded:
            raise
        except InvalidModelOutput as exc:
            records.extend(exc.records)
            error = f"invalid output: {exc}"[:200]
            if attempt == 0:
                messages.append({"role": "user", "content": RETRY.format(error=str(exc)[:300])})
            continue
        except CassetteMiss:
            raise
        except LLMError as exc:
            error = f"{type(exc).__name__}: {exc}"[:200]
            break
        records.extend(result.records)
        parsed = result.parsed
        call_id = records[-1].id if records else None
        if parsed is None:  # tools were not offered; treat a tool request as no answer
            error = "the verifier asked for tools"
            break
        answers: dict[str, EntailmentAnswer] = {}
        for item in parsed.items:
            if item.ref in refs and item.ref not in answers:
                answers[item.ref] = EntailmentAnswer(
                    item.supports, item.rationale[:MAX_RATIONALE_CHARS], call_id
                )
        missing = EntailmentAnswer(None, "no item for this ref in the verifier's answer", call_id)
        return {ref: answers.get(ref, missing) for ref in refs}
    call_id = records[-1].id if records else None
    return {ref: EntailmentAnswer(None, error, call_id) for ref in refs}
