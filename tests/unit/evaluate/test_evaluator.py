"""Evaluator sessions on tiny_service with FakeLLM: tools, prompt layout, ledger, consistency."""

import json
from pathlib import Path

from pydantic import JsonValue

from archlens.config import SessionLimits
from archlens.errors import BudgetExceeded
from archlens.evaluate.consistency import evaluate_llm_checks, merge_runs
from archlens.evaluate.deterministic import llm_checks, run_deterministic_checks
from archlens.evaluate.llm_session import Evaluator, EvaluatorPrompts, profile_summary, select_facts
from archlens.facts.base import make_fact
from archlens.llm.fake import FakeLLM
from archlens.llm.types import ToolCall
from archlens.models import (
    CheckResult,
    Citation,
    CodeEvidence,
    Fact,
    FactSet,
    LLMCheckOutput,
    MetricEvaluationOutput,
    Rubric,
    ScanEvidence,
    Severity,
    Verdict,
)
from archlens.rubric import load_rubrics
from tests.unit.conftest import TinyEnv
from tests.unit.rubric.helpers import make_ctx

REPO = Path(__file__).parents[3]
PROMPTS = EvaluatorPrompts.load(REPO / "prompts")
LIMITS = SessionLimits(max_tool_calls=14, max_context_tokens=60_000, max_facts=150)
USERS = "app/api/users.py"


def security() -> Rubric:
    return load_rubrics(REPO / "rubrics")["security"]


def evaluator(env: TinyEnv, llm: FakeLLM) -> Evaluator:
    return Evaluator(llm, env.tools, env.profile, env.facts, LIMITS, PROMPTS)


def call(call_id: str, name: str, **args: JsonValue) -> ToolCall:
    return ToolCall(call_id, name, json.dumps(args))


def result(check_id: str, verdict: Verdict, start: int = 34, end: int = 34) -> LLMCheckOutput:
    citations = [Citation(path=USERS, start_line=start, end_line=end)] if start else []
    return LLMCheckOutput(
        check_id=check_id, verdict=verdict, claim=f"{check_id} {verdict}", citations=citations,
        confidence="high",
    )  # fmt: skip


def key(*check_ids: str) -> tuple[str, ...]:
    return ("evaluator", "evaluator.system", *check_ids)


TOOL_TURN = [
    call("c1", "search_code", query=r"text\(f", mode="regex"),
    call("c2", "read_file", path=USERS, start_line=32, end_line=47),
]
ANSWER = MetricEvaluationOutput(
    results=[
        result("SEC-04", "partial", 38, 45),
        result("SEC-05", "fail"),
        result("SEC-06", "pass", 0),  # evidence-less pass → unknown
    ]
)


async def test_scripted_session_with_tool_calls(tiny_env: TinyEnv) -> None:
    rubric = security()
    checks = llm_checks(rubric, tiny_env.profile.flags)
    assert [c.id for c in checks] == ["SEC-04", "SEC-05", "SEC-06"]
    llm = FakeLLM({key("SEC-04", "SEC-05", "SEC-06"): [TOOL_TURN, ANSWER]})
    results = await evaluator(tiny_env, llm).evaluate(rubric, checks)

    assert [(r.check_id, r.verdict, r.reason) for r in results] == [
        ("SEC-04", "partial", None),
        ("SEC-05", "fail", None),
        ("SEC-06", "unknown", "no_evidence"),
    ]
    first = results[0]
    assert first.session_id == "security:evaluate:0" and first.prompt_version == PROMPTS.version
    assert [r.tool for r in first.search_log] == ["search_code", "read_file"]
    assert len(first.llm_call_ids) == 2 and first.model == "fake"
    assert first.citations == [Citation(path=USERS, start_line=38, end_line=45)]
    ledger = tiny_env.tools.ledger.lines("security:evaluate:0", USERS)
    assert set(range(32, 48)) <= ledger  # read_file + search preview (+ route fact evidence)


async def test_prompt_is_static_first_and_wraps_repo_content(tiny_env: TinyEnv) -> None:
    rubric = security()
    checks = llm_checks(rubric, tiny_env.profile.flags)
    llm = FakeLLM({key("SEC-04", "SEC-05", "SEC-06"): [ANSWER]})
    await evaluator(tiny_env, llm).evaluate(rubric, checks)
    request = llm.calls[0]
    system, metric, repo = (str(m["content"]) for m in request.messages)
    boundary = tiny_env.tools.boundary
    assert [m["role"] for m in request.messages] == ["system", "user", "user"]
    assert boundary not in system + metric and "app/" not in system + metric  # no repo content
    assert "about 14 tool calls" in system
    assert all(f"### {c.id}: {c.title}" in metric for c in checks)
    assert "fail/partial may have no citations" in metric  # SEC-06 is absence_allowed
    assert f'<repo_data boundary="{boundary}" source="profile">' in repo
    assert f'<repo_data boundary="{boundary}" source="facts">' in repo
    assert "kinds route, sast_finding, dependency" in repo
    assert "each of: SEC-04, SEC-05, SEC-06" in repo and "Evaluation run 1." in repo
    assert [t.name for t in request.tools] == [
        "list_dir", "read_file", "search_code", "find_symbol", "get_facts"
    ]  # fmt: skip
    assert request.tags == ("SEC-04", "SEC-05", "SEC-06") and request.metric == "security"
    route_lines = {
        e.start_line for f in tiny_env.facts.by_kind("route") for e in f.evidence
        if isinstance(e, CodeEvidence) and e.path == USERS
    }  # fmt: skip
    assert route_lines
    assert route_lines <= tiny_env.tools.ledger.lines("security:evaluate:0", USERS)


async def test_no_checks_means_no_session(tiny_env: TinyEnv) -> None:
    llm = FakeLLM()
    assert await evaluator(tiny_env, llm).evaluate(security(), []) == []
    assert llm.calls == []


async def test_session_failure_marks_all_checks(tiny_env: TinyEnv) -> None:
    rubric = security()
    checks = llm_checks(rubric, tiny_env.profile.flags)
    llm = FakeLLM({key("SEC-04", "SEC-05", "SEC-06"): [BudgetExceeded("budget reached")]})
    results = await evaluator(tiny_env, llm).evaluate(rubric, checks)
    assert {(r.verdict, r.reason) for r in results} == {("unknown", "budget")}


# --- self-consistency --------------------------------------------------------------------------


async def run_consistency(
    tiny_env: TinyEnv, rerun: MetricEvaluationOutput | Exception
) -> tuple[list[CheckResult], FakeLLM]:
    rubric = security()
    checks = llm_checks(rubric, tiny_env.profile.flags)
    llm = FakeLLM({key("SEC-04", "SEC-05", "SEC-06"): [ANSWER], key("SEC-05"): [rerun]})
    return await evaluate_llm_checks(evaluator(tiny_env, llm), rubric, checks), llm


async def test_critical_check_agreeing_rerun_keeps_the_first(tiny_env: TinyEnv) -> None:
    rerun = MetricEvaluationOutput(results=[result("SEC-05", "fail")])
    results, llm = await run_consistency(tiny_env, rerun)
    sec05 = next(r for r in results if r.check_id == "SEC-05")
    assert (sec05.verdict, sec05.attempt, sec05.confidence) == ("fail", 0, "high")
    assert [c.tags for c in llm.calls] == [("SEC-04", "SEC-05", "SEC-06"), ("SEC-05",)]
    assert "Evaluation run 2." in str(llm.calls[1].messages[-1]["content"])


async def test_critical_check_disagreement_is_unknown(tiny_env: TinyEnv) -> None:
    rerun = MetricEvaluationOutput(results=[result("SEC-05", "pass", 33, 35)])
    results, _ = await run_consistency(tiny_env, rerun)
    sec05 = next(r for r in results if r.check_id == "SEC-05")
    assert (sec05.verdict, sec05.reason, sec05.confidence) == ("unknown", "inconsistent", "low")
    assert sec05.claim == "run 1 fail: SEC-05 fail | run 2 pass: SEC-05 pass"
    assert len(sec05.llm_call_ids) == 2
    others = [r.verdict for r in results if r.check_id != "SEC-05"]
    assert others == ["partial", "unknown"]  # non-critical checks are not rerun


async def test_failed_rerun_is_not_a_disagreement(tiny_env: TinyEnv) -> None:
    results, _ = await run_consistency(tiny_env, BudgetExceeded("budget reached"))
    sec05 = next(r for r in results if r.check_id == "SEC-05")
    assert (sec05.verdict, sec05.confidence) == ("fail", "low")


async def test_reruns_of_the_same_checks_never_share_a_cache_key(tiny_env: TinyEnv) -> None:
    rubric = security()
    (sec05,) = [c for c in rubric.checks if c.id == "SEC-05"]
    answer = MetricEvaluationOutput(results=[result("SEC-05", "fail")])
    llm = FakeLLM({key("SEC-05"): [answer, answer]})
    await evaluate_llm_checks(evaluator(tiny_env, llm), rubric, [sec05])
    assert len(llm.calls) == 2 and llm.calls[0].messages != llm.calls[1].messages


def test_merge_with_three_runs() -> None:
    base = CheckResult(
        check_id="X-01", metric="m", origin="llm", verdict="fail", claim="a", confidence="high",
        rubric_version="1.0.0",
    )  # fmt: skip
    same = base.model_copy(update={"attempt": 1})
    other = base.model_copy(update={"attempt": 2, "verdict": "partial", "claim": "b"})
    assert merge_runs([base], [[same], [same]]) == [base]
    (merged,) = merge_runs([base], [[same], [other]])
    assert merged.reason == "inconsistent" and merged.claim.count("run ") == 3


# --- fact selection, profile, deterministic helpers --------------------------------------------


def fact(path: str, severity: Severity | None, n: int) -> Fact:
    evidence = [ScanEvidence(tool="t", tool_version="1", query=f"{path}{n}", result_count=1)]
    return make_fact("sast_finding", "t", {"path": path, "n": n}, evidence, severity)


def test_select_facts_by_severity_then_path_diversity() -> None:
    facts = [
        fact("b.py", "high", 1), fact("b.py", "high", 2), fact("b.py", "high", 3),
        fact("c.py", "high", 1), fact("d.py", None, 1), fact("a.py", "critical", 1),
    ]  # fmt: skip
    fact_set = FactSet(commit_sha="0" * 40, facts=facts, tool_runs=[])
    selected, total = select_facts(fact_set, ["sast_finding"], 4)
    assert total == 6
    assert [(f.attributes["path"], f.attributes["n"]) for f in selected] == [
        ("a.py", 1), ("b.py", 1), ("c.py", 1), ("b.py", 2)
    ]  # fmt: skip
    assert select_facts(fact_set, [], 10) == ([], 0)


def test_profile_summary(tiny_env: TinyEnv) -> None:
    text = profile_summary(tiny_env.profile)
    assert "frameworks: fastapi" in text and "has_http_api" in text
    assert "has_k8s" not in text  # only flags that hold


def test_deterministic_helpers(tmp_path: Path) -> None:
    rubric = security()
    assert llm_checks(rubric, {}) == []
    assert [c.id for c in llm_checks(rubric, {"has_http_api": True})] == ["SEC-04", "SEC-06"]
    results = run_deterministic_checks(rubric, make_ctx(tmp_path))
    assert [r.check_id for r in results] == ["SEC-01", "SEC-02", "SEC-03", "SEC-07"]
    retired = rubric.model_copy(
        update={"checks": [c.model_copy(update={"retired": True}) for c in rubric.checks]}
    )
    assert run_deterministic_checks(retired, make_ctx(tmp_path / "x")) == []
