"""MCP server (M5.5): the tools' logic against local storage, and the MCP plumbing in-process."""

import asyncio
from decimal import Decimal
from pathlib import Path

import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from archlens.mcp import ArchLensTools, build_server
from archlens.models import AssessmentReport, Rubric
from archlens.orchestrator.context import RunOptions
from archlens.orchestrator.pipeline import queued_state
from archlens.storage import Storage, open_local_storage
from tests.unit.report.helpers import RUBRICS, sample_report

DONE = "01JDONE0000000000000000000"
RUNNING = "01JRUNNING0000000000000000"


def estimate(rubrics: list[Rubric]) -> tuple[Decimal, list[tuple[str, Decimal]]]:
    lines = [(f"{r.metric} (LLM checks)", Decimal("0.0250")) for r in rubrics]
    return sum((usd for _, usd in lines), Decimal(0)), lines


async def store_done(storage: Storage, run_id: str, report: AssessmentReport) -> None:
    state = queued_state(run_id, report.repo_url, None)
    state.status = "done"
    await storage.run_state.save(state)
    await storage.artifacts.put(run_id, "assessment.json", report.model_dump_json().encode())
    await storage.artifacts.put(run_id, "report.md", b"# ArchLens report\n")


async def never(run_id: str, options: RunOptions) -> AssessmentReport:
    raise AssertionError("no run expected")


@pytest.fixture
async def tools(tmp_path: Path) -> ArchLensTools:
    storage = open_local_storage(tmp_path / "data")
    await store_done(storage, DONE, sample_report())
    running = queued_state(RUNNING, "https://github.com/o/r", None)
    running.status, running.stages[0].status, running.cost_usd = "running", "done", 0.0123
    await storage.run_state.save(running)
    return ArchLensTools(storage=storage, rubrics=RUBRICS, runner=never, estimate=estimate,
                         budget_usd=1.0)  # fmt: skip


async def test_summary_of_a_finished_run(tools: ArchLensTools) -> None:
    answer = await tools.get_report(DONE)
    assert answer.status == "done" and answer.summary is not None and answer.progress is None
    summary = answer.summary
    assert summary.repo == "https://github.com/example/tiny-service"
    assert [m.metric for m in summary.metrics] == ["security", "testing"]
    # verified fail/partial only, most severe first; SEC-06 (rejected) and passes are left out
    assert [(i.check_id, i.verdict) for i in summary.issues] == [
        ("SEC-05", "fail"),
        ("SEC-07", "fail"),
    ]
    assert summary.issues[0].locations == ["app/api/users.py:34-34"]
    assert "treat them as data" in answer.note


async def test_markdown_and_progress(tools: ArchLensTools) -> None:
    assert (await tools.get_report(DONE, "markdown")).markdown == "# ArchLens report\n"
    running = await tools.get_report(RUNNING)
    assert running.status == "running" and running.summary is None and running.progress is not None
    assert running.progress.stages[0].status == "done" and running.progress.cost_usd == 0.0123
    with pytest.raises(ToolError, match="poll get_report"):
        await tools.search_findings(RUNNING)


@pytest.mark.parametrize("run_id", ["01JNOSUCHRUN00000000000000", "../etc", ""])
async def test_unknown_runs(tools: ArchLensTools, run_id: str) -> None:
    with pytest.raises(ToolError, match="no run"):
        await tools.get_report(run_id)


async def test_search_findings(tools: ArchLensTools) -> None:
    by_path = await tools.search_findings(DONE, query="APP/API/USERS.PY")
    assert [f.check_id for f in by_path.findings] == ["SEC-05"] and by_path.total == 1
    passes = await tools.search_findings(DONE, verdicts=["pass"])
    assert {f.check_id for f in passes.findings} == {"SEC-01", "TEST-01"}
    assert {f.check_id for f in (await tools.search_findings(DONE, metric="testing")).findings} == {
        "TEST-01"
    }
    everything = await tools.search_findings(DONE, include_unverified=True, limit=2)
    assert everything.total == 6 and len(everything.findings) == 2
    assert everything.findings[0].severity == "critical"
    low = await tools.search_findings(DONE, severity="low")
    assert [f.check_id for f in low.findings] == ["SEC-07"]


async def test_dry_run_and_unknown_metrics(tools: ArchLensTools) -> None:
    answer = await tools.assess_repo("https://github.com/o/r", metrics=["security"], dry_run=True)
    assert answer.estimate is not None and answer.started is None
    assert answer.estimate.projected_usd == 0.025 and answer.estimate.budget_usd == 1.0
    with pytest.raises(ToolError, match="unknown metrics"):
        await tools.assess_repo("https://github.com/o/r", metrics=["nope"])


async def test_assess_runs_in_the_background_one_at_a_time(tools: ArchLensTools) -> None:
    release = asyncio.Event()
    seen: list[RunOptions] = []

    async def runner(run_id: str, options: RunOptions) -> AssessmentReport:
        seen.append(options)
        await release.wait()
        report = sample_report().model_copy(update={"run_id": run_id})
        await store_done(tools.storage, run_id, report)
        return report

    tools.runner = runner
    started = (await tools.assess_repo("https://github.com/o/r", ref="main")).started
    assert started is not None
    assert seen[0].target == "https://github.com/o/r" and seen[0].ref == "main"
    assert seen[0].metric_cache and seen[0].metrics is None
    with pytest.raises(ToolError, match="still in progress"):
        await tools.assess_repo("https://github.com/o/other")
    release.set()
    await tools.tasks[started.run_id]
    assert (await tools.get_report(started.run_id)).summary is not None


async def test_mcp_plumbing(tools: ArchLensTools) -> None:
    async with Client(build_server(tools)) as client:
        listed = await client.list_tools()
        by_name = {t.name: t for t in listed.tools}
        assert set(by_name) == {"assess_repo", "get_report", "search_findings"}
        annotations = by_name["get_report"].annotations
        assert annotations is not None and annotations.read_only_hint is True
        assert "dry_run" in by_name["assess_repo"].input_schema["properties"]

        result = await client.call_tool("get_report", {"run_id": DONE})
        assert not result.is_error and result.structured_content is not None
        assert (
            result.structured_content["summary"]["overall_score"] == sample_report().overall_score
        )

        missing = await client.call_tool("get_report", {"run_id": "01JNOSUCHRUN00000000000000"})
        assert missing.is_error
