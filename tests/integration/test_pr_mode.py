"""PR mode, offline (M5.2 AC): with the metric result cache on, a second run re-evaluates only the
metrics whose inputs changed, and writes `pr_comment.md` against the base report."""

from pathlib import Path

from archlens.evaluate.result_cache import FILES_NAMESPACE, FileHashes
from archlens.llm.fake import FakeLLM
from archlens.models import AssessmentReport
from archlens.orchestrator.context import RunOptions
from archlens.orchestrator.pipeline import Pipeline
from tests.fixture_repos import MaterializedRepo
from tests.integration.test_assess_offline import context, evaluator_script, later_script


async def assess(
    repo: MaterializedRepo, tmp: Path, run_id: str, base: AssessmentReport | None = None
) -> tuple[AssessmentReport, Pipeline, FakeLLM]:
    llm = FakeLLM(evaluator_script() | later_script())
    options = RunOptions(target=str(repo.root), scanners=False, metric_cache=True, base=base)
    pipeline = Pipeline(context(tmp, llm, run_id), options)  # same data dir → same cache
    return await pipeline.run(), pipeline, llm


def evaluated_checks(llm: FakeLLM) -> set[str]:
    return {tag for call in llm.calls if call.role == "evaluator" for tag in call.tags}


def evaluated_metrics(llm: FakeLLM) -> set[str]:
    return {call.metric for call in llm.calls if call.role == "evaluator" and call.metric}


async def test_only_metrics_with_changed_inputs_are_re_evaluated(
    tiny_service: MaterializedRepo, tmp_path: Path
) -> None:
    base, first, llm = await assess(tiny_service, tmp_path, "base")
    assert first.reused == [] and {"CI-06", "AUTH-01"} <= evaluated_checks(llm)

    # a code change: every metric scoped to "**/*" sees it; cicd (".github/**", …) doesn't
    notify = tiny_service.root / "app" / "services" / "notify.py"
    notify.write_text(notify.read_text() + "\n# reviewed\n")
    head, second, llm = await assess(tiny_service, tmp_path, "head", base)

    assert second.reused == ["cicd"]

    # each PR-mode run records its files; the PR comment diffs the head against the base's
    async def recorded(commit: str) -> FileHashes:
        data = await second.ctx.storage.cache.get(FILES_NAMESPACE, commit)
        assert data is not None
        return FileHashes.model_validate_json(data)

    before, after = await recorded(base.commit_sha), await recorded(head.commit_sha)
    assert before.changed(after) == {"app/services/notify.py"}
    checks = evaluated_checks(llm)
    assert "AUTH-01" in checks and not checks & {"CI-06", "CI-07"}
    # the reused results still go through verification and keep their findings
    assert {f.result.check_id for f in head.findings} == {f.result.check_id for f in base.findings}
    comment = await second.ctx.storage.artifacts.get("head", "pr_comment.md")
    assert comment is not None
    assert "(unchanged inputs, reused: cicd)" in comment.decode()
    assert "No verified finding changed." in comment.decode()


async def test_an_unchanged_repository_reuses_every_llm_metric(
    tiny_service: MaterializedRepo, tmp_path: Path
) -> None:
    base, _, first = await assess(tiny_service, tmp_path, "base")
    _, again, llm = await assess(tiny_service, tmp_path, "again", base)
    assert evaluated_checks(llm) == set()
    assert set(again.reused) == evaluated_metrics(first) and len(again.reused) >= 8


async def test_cache_is_off_by_default(tiny_service: MaterializedRepo, tmp_path: Path) -> None:
    await assess(tiny_service, tmp_path, "base")
    llm = FakeLLM(evaluator_script() | later_script())
    options = RunOptions(str(tiny_service.root), scanners=False)
    pipeline = Pipeline(context(tmp_path, llm, "plain"), options)
    await pipeline.run()
    assert pipeline.reused == [] and "CI-06" in evaluated_checks(llm)
