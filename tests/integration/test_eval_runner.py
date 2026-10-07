"""Eval runner end to end on tiny_service with FakeLLM and toy mutations (offline)."""

import random
import shutil
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from archlens.errors import ArchLensError
from archlens.eval.config import EvalConfig, SuiteEntry, VariantRun
from archlens.eval.mutation import ChangedLines, Mutation, MutationResult
from archlens.eval.repos import EvalRepo, checkout_path, marker_path
from archlens.eval.runner import EvalRunner, build_plan, load_results
from archlens.eval.variants import VariantSpec
from archlens.index.embed import FakeEmbedder
from archlens.llm.fake import FakeLLM
from archlens.llm.untrusted import new_boundary
from archlens.models import FactSet, RepoProfile
from archlens.orchestrator.context import RunContext
from archlens.rubric import load_rubrics
from archlens.storage import open_local_storage
from tests.fixture_repos import MaterializedRepo
from tests.unit.llm.helpers import repo_config

pytestmark = pytest.mark.integration
REPO = Path(__file__).parents[2]
RUBRICS = load_rubrics(REPO / "rubrics")
PRIMARY = EvalRepo(
    id="primary", url="https://github.com/o/tiny", commit="c" * 40, license="MIT", langs=["python"]
)


def has_readme(facts: FactSet, profile: RepoProfile) -> bool:
    return any(f.kind == "doc_file" and f.attributes.get("type") == "readme" for f in facts.facts)


def never(facts: FactSet, profile: RepoProfile) -> bool:
    return False


def truncate_readme(repo: Path, rng: random.Random) -> MutationResult:
    (repo / "README.md").write_text("# tiny-service\n")
    return MutationResult((ChangedLines("README.md", 1, 1),))


def untouched(repo: Path, rng: random.Random) -> MutationResult:
    raise AssertionError("a mutation whose precondition failed was applied")


MUTATIONS = {
    "M-README": Mutation("M-README", {"DOC-01": "fail"}, "truncate README", has_readme, truncate_readme, absence=True),
    "M-NEVER": Mutation("M-NEVER", {"CTR-01": "fail"}, "never applies", never, untouched),
}  # fmt: skip


@pytest.fixture
def cache(tiny_service: MaterializedRepo, tmp_path: Path) -> Path:
    """A fetched-looking cache holding tiny_service as `primary`."""
    cache = tmp_path / "cache"
    dest = checkout_path(PRIMARY, cache)
    shutil.copytree(tiny_service.root, dest)
    marker_path(dest).write_text(PRIMARY.commit + "\n")
    return cache


def runner(tmp_path: Path, cache: Path, budget: float, run_id: str = "full-1") -> EvalRunner:
    config = EvalConfig(
        name="full",
        budget_usd=budget,
        suite=[SuiteEntry(repo="primary", runs=2), SuiteEntry(repo="primary", variant="V1")],
    )
    variants = [VariantSpec(id="V1", repo="primary", mutations=["M-README", "M-NEVER"])]
    plan = build_plan(
        config, repos=[PRIMARY], variants=variants, mutations=MUTATIONS, rubrics=RUBRICS
    )
    storage = open_local_storage(tmp_path / "data")

    def context(run_id: str, use_cache: bool) -> RunContext:
        return RunContext(
            run_id=run_id, config=repo_config(), storage=storage, llm=FakeLLM(),
            embedder=FakeEmbedder(), boundary=new_boundary(),
            work_dir=tmp_path / "data" / "runs" / run_id, tools_dir=tmp_path / "tools",
            rubrics=RUBRICS, prompts_dir=REPO / "prompts",
            clock=lambda: datetime(2026, 10, 7, tzinfo=UTC),
        )  # fmt: skip

    return EvalRunner(
        plan=plan, mutations=MUTATIONS, cache_dir=cache, results_dir=tmp_path / "results",
        context_factory=context, eval_run_id=run_id, app_config=repo_config(),
        tools_dir=tmp_path / "tools", scanners=False,
    )  # fmt: skip


async def test_runs_variants_on_fresh_copies(cache: Path, tmp_path: Path) -> None:
    base_readme = (checkout_path(PRIMARY, cache) / "README.md").read_text()
    outcome = await runner(tmp_path, cache, budget=1.0).run(Decimal("0.01"))
    out = tmp_path / "results" / "full-1"
    assert sorted(p.name for p in out.iterdir()) == [
        "config.yaml",
        "summary.json",
        "table.md",
        "variants",
    ]
    assert [r.name for r in load_results(out)] == [
        "primary.V1.0",
        "primary.base.0",
        "primary.base.1",
    ]
    v1: VariantRun = next(r for r in outcome.records if r.variant == "V1")
    assert v1.commit == f"{PRIMARY.commit}+V1" and v1.skipped == ["M-NEVER"]
    assert [m.id for m in v1.mutations] == ["M-README"] and v1.mutations[0].changed[
        0
    ].path == "README.md"
    assert (
        checkout_path(PRIMARY, cache) / "README.md"
    ).read_text() == base_readme  # base untouched
    # the deterministic layer saw the mutation: the truncated README is the only doc file left
    assert outcome.summary.recall["primary"].pairs == 1
    assert "## Eval results: `full`" in (out / "table.md").read_text()


async def test_budget_cap_skips_runs(cache: Path, tmp_path: Path) -> None:
    capped = await runner(tmp_path, cache, budget=0.05).run(Decimal("0.06"))
    assert capped.records == []
    assert capped.skipped == ["primary.base.0", "primary.base.1", "primary.V1.0"]
    exact = await runner(tmp_path, cache, budget=0.05, run_id="full-2").run(Decimal("0.05"))
    assert len(exact.records) == 3 and exact.skipped == []  # spent (0 with FakeLLM) + 0.05 ≤ 0.05


async def test_results_are_append_only(cache: Path, tmp_path: Path) -> None:
    (tmp_path / "results" / "full-1").mkdir(parents=True)
    with pytest.raises(ArchLensError, match="append-only"):
        await runner(tmp_path, cache, budget=1.0).run(Decimal("0.01"))


async def test_unfetched_repo_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ArchLensError, match="archlens eval fetch"):
        await runner(tmp_path, tmp_path / "empty-cache", budget=1.0).run(Decimal("0.01"))
