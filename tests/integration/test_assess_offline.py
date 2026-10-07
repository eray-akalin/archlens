"""`archlens assess` end to end on tiny_service, offline (M2.8).

Real ingest, extractors, profile, index, rules, tools, ledger, verifier, scorer and renderers; the
LLM is a FakeLLM whose scripted answers cite the planted defects after reading them through the
tools, so every verdict still has to survive the mechanical check against the session's ledger.
Scanners are off here (`-m scanners` covers them), so SEC-01..03 and CI-03 must come out unknown.
"""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from archlens.errors import BudgetExceeded
from archlens.index.embed import FakeEmbedder
from archlens.llm.fake import FakeLLM, Scripted
from archlens.llm.types import LLMRequest, LLMResult, ToolCall
from archlens.llm.untrusted import new_boundary
from archlens.models import (
    AssessmentReport,
    Citation,
    EntailmentBatchOutput,
    EntailmentItem,
    LLMCheckOutput,
    MetricEvaluationOutput,
    Narrative,
    SkepticOutput,
    Verdict,
)
from archlens.orchestrator.context import RunContext, RunOptions
from archlens.orchestrator.pipeline import run_assessment
from archlens.rubric import load_rubrics
from archlens.storage import Storage, open_local_storage
from tests.fixture_repos import MaterializedRepo, answer_key
from tests.unit.llm.helpers import repo_config

pytestmark = pytest.mark.integration
REPO = Path(__file__).parents[2]
USERS, MAIN = "app/api/users.py", "app/main.py"
TESTS, CI = "tests/test_users.py", ".github/workflows/ci.yml"
MODELS, DB, README = "app/models.py", "app/db.py", "README.md"
SCANNER_CHECKS = {"SEC-01", "SEC-02", "SEC-03", "CI-03", "STR-03", "CTR-07"}
METRICS = (
    "auth", "cicd", "container", "data", "documentation", "logging", "performance", "security",
    "structure", "testing",
)  # fmt: skip
RERUNS = {("SEC-05",), ("AUTH-01",)}  # critical LLM checks get a consistency session


def read(call_id: str, path: str) -> ToolCall:
    return ToolCall(call_id, "read_file", json.dumps({"path": path}))


def out(check_id: str, verdict: Verdict, path: str, start: int, end: int) -> LLMCheckOutput:
    return LLMCheckOutput(
        check_id=check_id, verdict=verdict, claim=f"{check_id}: planted defect at {path}:{start}",
        citations=[Citation(path=path, start_line=start, end_line=end)], confidence="high",
    )  # fmt: skip


def unknown(check_id: str) -> LLMCheckOutput:
    return LLMCheckOutput(
        check_id=check_id, verdict="unknown", claim="not judged in this scripted run",
        citations=[], confidence="low",
    )  # fmt: skip


def absent(check_id: str) -> LLMCheckOutput:
    return LLMCheckOutput(
        check_id=check_id, verdict="fail", claim=f"{check_id}: nothing of the kind exists",
        citations=[], confidence="medium",
    )  # fmt: skip


def yes(*refs: str) -> EntailmentBatchOutput:
    return EntailmentBatchOutput(
        items=[EntailmentItem(ref=r, supports="yes", rationale="the code shows it") for r in refs]
    )


def evaluator_script() -> dict[tuple[str, ...], list[Scripted]]:
    ev = ("evaluator", "evaluator.system")

    def answer(*results: LLMCheckOutput) -> MetricEvaluationOutput:
        return MetricEvaluationOutput(results=list(results))

    return {
        (*ev, "STR-01", "STR-05"): [answer(unknown("STR-01"), unknown("STR-05"))],
        (*ev, "AUTH-01", "AUTH-02", "AUTH-03", "AUTH-04", "AUTH-05", "AUTH-06"): [
            [read("a1", USERS), read("a2", MODELS)],
            answer(
                out("AUTH-01", "fail", USERS, 50, 54), unknown("AUTH-02"), unknown("AUTH-03"),
                unknown("AUTH-04"), out("AUTH-05", "fail", MODELS, 13, 13), unknown("AUTH-06"),
            ),
        ],
        (*ev, "AUTH-01"): [answer(out("AUTH-01", "fail", USERS, 50, 54))],
        (*ev, "SEC-04", "SEC-05", "SEC-06"): [
            [read("s1", USERS), read("s2", MAIN)],
            answer(
                out("SEC-04", "partial", USERS, 38, 45),
                out("SEC-05", "fail", USERS, 32, 35),
                out("SEC-06", "fail", MAIN, 9, 15),
            ),
        ],
        (*ev, "SEC-05"): [answer(out("SEC-05", "fail", USERS, 34, 34))],
        (*ev, "DATA-02", "DATA-03", "DATA-04", "DATA-05"): [
            [read("d1", DB), read("d2", MODELS)],
            answer(
                out("DATA-02", "fail", DB, 4, 4), unknown("DATA-03"), unknown("DATA-04"),
                out("DATA-05", "fail", MODELS, 13, 13),
            ),
        ],
        (*ev, "LOG-03", "LOG-04"): [
            [read("l1", USERS)],
            answer(absent("LOG-03"), out("LOG-04", "fail", USERS, 18, 18)),
        ],
        (*ev, "TEST-05"): [[read("t1", TESTS)], answer(out("TEST-05", "fail", TESTS, 8, 15))],
        (*ev, "CI-06", "CI-07"): [
            [read("c1", CI)],
            answer(out("CI-06", "fail", CI, 15, 26), out("CI-07", "fail", CI, 20, 24)),
        ],
        (*ev, "PERF-01", "PERF-02", "PERF-03", "PERF-04", "PERF-05"): [
            [read("p1", USERS)],
            answer(
                out("PERF-01", "fail", USERS, 57, 60), out("PERF-02", "fail", USERS, 22, 29),
                unknown("PERF-03"), out("PERF-04", "fail", USERS, 22, 24),
                out("PERF-05", "fail", USERS, 59, 59),
            ),
        ],
        (*ev, "DOC-01", "DOC-06"): [
            [read("r1", README)],
            answer(out("DOC-01", "partial", README, 1, 5), out("DOC-06", "fail", README, 5, 5)),
        ],
    }  # fmt: skip


def later_script() -> dict[tuple[str, ...], list[Scripted]]:
    """Verifier, skeptic and synthesizer answers."""
    batches = [
        ("AUTH-01", "AUTH-05"), ("SEC-04", "SEC-05", "SEC-06"), ("DATA-02", "DATA-05"),
        ("LOG-04",), ("TEST-05",), ("CI-06", "CI-07"),
        ("PERF-01", "PERF-02", "PERF-04", "PERF-05"), ("DOC-01", "DOC-06"),
    ]  # fmt: skip
    script: dict[tuple[str, ...], list[Scripted]] = {
        ("verifier", "verifier.entailment", *refs): [yes(*refs)] for refs in batches
    }
    keep = SkepticOutput(refuted=False, reason="the cited code is reached", citations=[])
    script[("skeptic", "skeptic.system", "SEC-05")] = [keep]
    script[("skeptic", "skeptic.system", "AUTH-01")] = [keep]
    script[("synth", "synth.narrative")] = [
        Narrative(
            executive_summary="Security has string-built SQL and permissive CORS; CI never "
            "runs the tests.",
            per_metric=[],
            cited_findings=[],
        )
    ]
    return script


def context(root: Path, llm: FakeLLM, run_id: str) -> RunContext:
    data = root / "data"
    return RunContext(
        run_id=run_id,
        config=repo_config(),
        storage=open_local_storage(data),
        llm=llm,
        embedder=FakeEmbedder(),
        boundary=new_boundary(),
        work_dir=data / "runs" / run_id,
        tools_dir=root / "tools",
        rubrics=load_rubrics(REPO / "rubrics"),
        prompts_dir=REPO / "prompts",
        clock=lambda: datetime(2026, 10, 7, 12, 0, tzinfo=UTC),
    )


async def run(
    repo: MaterializedRepo, tmp: Path, llm: FakeLLM, *, run_id: str = "e2e", resume: bool = False
) -> tuple[AssessmentReport, RunContext]:
    ctx = context(tmp, llm, run_id)
    options = RunOptions(target=str(repo.root), scanners=False)
    return await run_assessment(ctx, options, resume=resume), ctx


class KillTesting(FakeLLM):
    """Dies in the testing session once the other metrics are recorded as done."""

    def __init__(self, script: dict[tuple[str, ...], list[Scripted]], storage: Storage) -> None:
        super().__init__(script)
        self.storage = storage

    async def complete[T: BaseModel](self, request: LLMRequest[T]) -> LLMResult[T]:
        if request.tags[:1] == ("TEST-05",):
            others = set(METRICS) - {"testing"}
            for _ in range(2000):  # until the other metrics are fully recorded, not just written
                state = await self.storage.run_state.load("r1")
                if state is not None and others <= set(state.metrics_done):
                    break
                await asyncio.sleep(0.005)
            raise RuntimeError("killed")
        return await super().complete(request)


async def test_assess_detects_the_planted_defects(
    tiny_service: MaterializedRepo, tmp_path: Path
) -> None:
    llm = FakeLLM(evaluator_script() | later_script())
    report, ctx = await run(tiny_service, tmp_path, llm)

    # the canonical artifact round-trips and the renderings exist
    stored = await ctx.storage.artifacts.get("e2e", "assessment.json")
    assert stored is not None and AssessmentReport.model_validate_json(stored) == report
    assert sorted(await ctx.storage.artifacts.names("e2e")) == [
        "assessment.json", "report.html", "report.md"
    ]  # fmt: skip
    assert [m.metric for m in report.metric_scores] == list(METRICS)

    findings = {f.result.check_id: f for f in [*report.findings, *report.other_findings]}
    rows = answer_key()
    assert len(rows) == 47
    for row in rows:
        finding = findings.get(row.check)
        if finding is None:  # the check doesn't apply to this repo at all (C05)
            assert row.expected == "not_applicable", row.id
            continue
        if row.check in SCANNER_CHECKS:
            assert finding.result.verdict == "unknown", row.id
            continue
        assert (finding.result.verdict, finding.verification.status) == (
            row.expected, "verified"
        ), (row.id, finding.result.claim, finding.verification.steps)  # fmt: skip
        assert finding.scored

    auth01 = findings["AUTH-01"]
    assert [s.step for s in auth01.verification.steps] == ["mechanical", "entailment", "skeptic"]
    log03 = findings["LOG-03"]  # evidence-less fail confirmed by the absence probes
    assert [s.step for s in log03.verification.steps] == ["absence"]
    assert report.overall_score is not None  # all ten metrics scored
    sec05 = findings["SEC-05"]
    assert [s.step for s in sec05.verification.steps] == ["mechanical", "entailment", "skeptic"]
    assert report.narrative is not None and "string-built SQL" in report.narrative.executive_summary
    security = next(m for m in report.metric_scores if m.metric == "security")
    assert security.capped_by == ["SEC-05"] and security.score is not None and security.score <= 4.0
    assert not any(v in report.model_dump_json() for v in tiny_service.secret_values)

    state = await ctx.storage.run_state.load("e2e")
    assert state is not None and state.status == "done"
    assert [s.status for s in state.stages] == ["done"] * 8
    assert state.metrics_done == list(METRICS)


async def test_killed_mid_evaluate_then_resumed(
    tiny_service: MaterializedRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = open_local_storage(tmp_path / "data")
    with pytest.raises(RuntimeError, match="killed"):
        await run(tiny_service, tmp_path, KillTesting(evaluator_script(), storage), run_id="r1")
    state = await storage.run_state.load("r1")
    assert state is not None and state.status == "failed"
    stages = {s.stage: s for s in state.stages}
    assert [stages[s].status for s in ("ingest", "facts", "profile", "index")] == ["done"] * 4
    assert stages["evaluate"].status == "failed" and "killed" in (stages["evaluate"].error or "")
    assert state.metrics_done == sorted(set(METRICS) - {"testing"})

    # resume: finished stages and metrics must not run again
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("facts were re-extracted on resume")

    monkeypatch.setattr("archlens.orchestrator.pipeline.collect_facts", forbidden)
    remaining = {k: v for k, v in evaluator_script().items() if k[2] == "TEST-05"}
    llm = FakeLLM(remaining | later_script())
    report, resumed = await run(tiny_service, tmp_path, llm, run_id="r1", resume=True)
    assert {c.tags for c in llm.calls if c.role == "evaluator"} == {("TEST-05",)}
    # checkpointed sessions were restored with their seen lines: their citations still verify
    for finding in report.findings + report.other_findings:
        if finding.result.origin == "llm" and finding.result.verdict != "unknown":
            assert finding.verification.status == "verified", finding.result.check_id
    state = await resumed.storage.run_state.load("r1")
    assert state is not None and state.status == "done" and state.metrics_done == list(METRICS)
    assert {"evaluate", "verify", "report"} <= set(report.cost.by_stage)  # both runs' calls


async def test_budget_exhaustion_degrades_to_unknown(
    tiny_service: MaterializedRepo, tmp_path: Path
) -> None:
    budget = BudgetExceeded("run budget reached")
    script: dict[tuple[str, ...], list[Scripted]] = {
        key: [budget] for key in evaluator_script() if key[2:] not in RERUNS
    }
    script[("synth", "synth.narrative")] = [budget]
    report, _ = await run(tiny_service, tmp_path, FakeLLM(script))
    llm_results = [f for f in report.other_findings if f.result.origin == "llm"]
    assert llm_results and {f.result.reason for f in llm_results} == {"budget"}
    assert report.narrative is None
    deterministic = {f.result.check_id: f.result.verdict for f in report.findings}
    assert deterministic["CI-04"] == "fail" and deterministic["TEST-01"] == "pass"


def test_unknown_metric_is_rejected(tiny_service: MaterializedRepo, tmp_path: Path) -> None:
    from archlens.errors import ArchLensError
    from archlens.orchestrator.pipeline import Pipeline

    ctx = context(tmp_path, FakeLLM(), "x")
    with pytest.raises(ArchLensError, match="unknown metrics"):
        Pipeline(ctx, RunOptions(target=str(tiny_service.root), metrics=("nope",)))


async def test_run_ids_and_resume_guards(tiny_service: MaterializedRepo, tmp_path: Path) -> None:
    from archlens.errors import ArchLensError, IngestError

    with pytest.raises(ArchLensError, match="no run ghost to resume"):
        await run(tiny_service, tmp_path, FakeLLM(), run_id="ghost", resume=True)
    llm = FakeLLM(evaluator_script() | later_script())
    await run(tiny_service, tmp_path, llm, run_id="once")
    with pytest.raises(ArchLensError, match="already exists"):
        await run(tiny_service, tmp_path, FakeLLM(), run_id="once")
    (tiny_service.root / "app" / "main.py").write_text("changed = True\n")
    with pytest.raises(IngestError, match="repository changed since run once started"):
        await run(tiny_service, tmp_path, FakeLLM(), run_id="once", resume=True)
