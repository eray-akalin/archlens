"""Tool-calling session loop (docs/LLM.md §4-5) driven by FakeLLM."""

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from archlens.config import SessionLimits
from archlens.errors import BudgetExceeded, CassetteMiss, InvalidModelOutput, ProviderError
from archlens.ingest.snapshot import build_snapshot
from archlens.llm.fake import FakeLLM
from archlens.llm.session import (
    BUDGET_EXHAUSTED,
    NO_TOOLS_LEFT,
    TOOL_REFUSED,
    SessionOutcome,
    run_tool_session,
)
from archlens.llm.types import ToolCall
from archlens.llm.untrusted import new_boundary
from archlens.models import FactSet, IngestLimits
from archlens.tools.repo_tools import RepoTools, ToolSession

KEY = ("evaluator", "demo")


class Answer(BaseModel):
    text: str


@pytest.fixture
def tools(tmp_path: Path) -> ToolSession:
    (tmp_path / "app.py").write_text("def f():\n    return 1\n")
    snapshot = build_snapshot(tmp_path, limits=IngestLimits())
    facts = FactSet(commit_sha="0" * 40, facts=[], tool_runs=[])
    return RepoTools(tmp_path, snapshot.files, facts, boundary=new_boundary()).session("s")


def read(call_id: str, start: int = 1, end: int = 1) -> ToolCall:
    args = {"path": "app.py", "start_line": start, "end_line": end}
    return ToolCall(call_id, "read_file", json.dumps(args))


async def run(
    llm: FakeLLM, tools: ToolSession, *, max_tool_calls: int = 5, max_context_tokens: int = 60_000
) -> SessionOutcome[Answer]:
    return await run_tool_session(
        llm,
        role="evaluator",
        stage="evaluate",
        prompt_version="demo@1.0.0+00000000",
        messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}],
        response_format=Answer,
        tools=tools,
        limits=SessionLimits(max_tool_calls=max_tool_calls, max_context_tokens=max_context_tokens),
    )


async def test_tool_calls_run_in_order_then_answer(tools: ToolSession) -> None:
    llm = FakeLLM({KEY: [[read("c1", 1, 1), read("c2", 2, 2)], Answer(text="done")]})
    outcome = await run(llm, tools)
    assert outcome.parsed == Answer(text="done") and outcome.error is None
    assert outcome.tool_calls == 2 and len(outcome.records) == 2
    tool_messages = [m for m in outcome.messages if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["c1", "c2"]
    assert "   1│ def f():" in str(tool_messages[0]["content"])
    assert [r.args["start_line"] for r in tools.search_log] == [1, 2]
    assert tools.tools.ledger.lines("s", "app.py") == {1, 2}
    assert all(request.tools for request in llm.calls)  # tools offered on every normal turn


async def test_tool_budget_refuses_extra_calls_and_forces_an_answer(tools: ToolSession) -> None:
    llm = FakeLLM({KEY: [[read("c1"), read("c2", 2, 2), read("c3")], Answer(text="ok")]})
    outcome = await run(llm, tools, max_tool_calls=2)
    assert outcome.parsed == Answer(text="ok")
    tool_messages = [m for m in outcome.messages if m["role"] == "tool"]
    assert tool_messages[2]["content"] == TOOL_REFUSED
    assert len(tools.search_log) == 2
    final = llm.calls[-1]
    assert final.tools == () and final.messages[-1] == {"role": "user", "content": BUDGET_EXHAUSTED}


async def test_context_budget_forces_an_answer(tools: ToolSession) -> None:
    llm = FakeLLM({KEY: [Answer(text="ok")]})
    outcome = await run(llm, tools, max_context_tokens=5)
    assert outcome.parsed is not None
    assert llm.calls[0].tools == () and llm.calls[0].messages[-1]["content"] == BUDGET_EXHAUSTED


async def test_tools_asked_for_after_the_budget(tools: ToolSession) -> None:
    llm = FakeLLM({KEY: [[read("c1")], [read("c2")], Answer(text="ok")]})
    outcome = await run(llm, tools, max_tool_calls=1)
    assert outcome.parsed == Answer(text="ok")
    assert llm.calls[-1].messages[-1]["content"] == NO_TOOLS_LEFT
    stubborn = FakeLLM({KEY: [[read("c1")], [read("c2")], [read("c3")]]})
    assert (await run(stubborn, tools, max_tool_calls=1)).error == "invalid_output"


async def test_invalid_answer_is_retried_once(tools: ToolSession) -> None:
    llm = FakeLLM(
        {KEY: [InvalidModelOutput("missing field text", content='{"txt": 1}'), Answer(text="ok")]}
    )
    outcome = await run(llm, tools)
    assert outcome.parsed == Answer(text="ok")
    retry = llm.calls[-1].messages
    assert retry[-2] == {"role": "assistant", "content": '{"txt": 1}'}
    assert "missing field text" in str(retry[-1]["content"])


async def test_invalid_answer_twice_fails_the_session(tools: ToolSession) -> None:
    llm = FakeLLM({KEY: [InvalidModelOutput("bad"), InvalidModelOutput("still bad")]})
    outcome = await run(llm, tools)
    assert (outcome.parsed, outcome.error) == (None, "invalid_output")
    assert outcome.detail == "still bad"
    assert len(outcome.records) == 0  # FakeLLM attaches no records to scripted exceptions


@pytest.mark.parametrize(
    ("exc", "error"),
    [
        (BudgetExceeded("run budget $1.00 reached"), "budget"),
        (ProviderError("HTTP 500", retryable=True, status=500), "llm_error"),
    ],
)
async def test_model_side_failures_become_outcomes(
    tools: ToolSession, exc: Exception, error: str
) -> None:
    llm = FakeLLM({KEY: [[read("c1")], exc]})
    outcome = await run(llm, tools)
    assert (outcome.parsed, outcome.error, outcome.tool_calls) == (None, error, 1)
    assert len(outcome.records) == 1  # the first turn's call is kept


async def test_cassette_miss_is_never_swallowed(tools: ToolSession) -> None:
    with pytest.raises(CassetteMiss):
        await run(FakeLLM({KEY: [CassetteMiss("no recording")]}), tools)
