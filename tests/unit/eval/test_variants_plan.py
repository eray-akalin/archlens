"""Variant constraints (EVALUATION.md §2.1), plan expansion and the cost projection."""

import random
from decimal import Decimal
from pathlib import Path

import pytest

from archlens.eval.config import EvalConfig, Features, SuiteEntry
from archlens.eval.mutation import ChangedLines, Mutation, MutationResult
from archlens.eval.repos import EvalRepo
from archlens.eval.runner import build_plan, project
from archlens.eval.variants import VariantError, VariantSpec, apply_variant, static_problems
from archlens.models import FactSet, RepoProfile, Verdict
from archlens.rubric import load_rubrics
from tests.unit.llm.helpers import repo_config

REPO = Path(__file__).parents[3]
RUBRICS = load_rubrics(REPO / "rubrics")
CHECK_METRIC = {c.id: r.metric for r in RUBRICS.values() for c in r.checks}


def always(facts: FactSet, profile: RepoProfile) -> bool:
    return True


def mutation(mutation_id: str, expected: dict[str, Verdict], *files: str) -> Mutation:
    def apply(repo: Path, rng: random.Random) -> MutationResult:
        for path in files:
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                target.read_text() + f"# {mutation_id}\n"
                if target.exists()
                else f"# {mutation_id}\n"
            )
        return MutationResult(tuple(ChangedLines(p, 1, 1) for p in files))

    return Mutation(
        id=mutation_id, expected=expected, description="", precondition=always, apply=apply
    )


MUTATIONS = {
    m.id: m
    for m in [
        mutation("M-SECRET", {"SEC-01": "fail"}, "app/config.py"),
        mutation("M-VULNDEP", {"SEC-02": "fail"}, "requirements.txt"),
        mutation("M-ROOT", {"CTR-01": "fail"}, "Dockerfile"),
        mutation("M-LATEST", {"CTR-02": "fail"}, "Dockerfile"),
        mutation("M-LOGSECRET", {"LOG-04": "fail"}, "app/login.py"),
        mutation("M-NOTESTS", {"TEST-01": "fail", "CI-02": "partial"}, "tests/test_a.py"),
        mutation("M-CIPERMS", {"CI-04": "fail"}, ".github/workflows/ci.yml"),
        mutation("M-SQLI", {"SEC-05": "fail"}, "app/items.py"),
        mutation("M-AUTHOFF", {"AUTH-01": "fail"}, "app/users.py"),
        mutation("I-COMMENT", {}, "app/items.py"),
        mutation("I-README", {}, "README.md"),
    ]
}


def variant(*mutations: str, variant_id: str = "V1") -> VariantSpec:
    return VariantSpec(id=variant_id, repo="primary", mutations=list(mutations))


# --- static rules ------------------------------------------------------------------------------


def test_valid_variant_has_no_problems() -> None:
    assert (
        static_problems(variant("M-SECRET", "M-ROOT", "M-CIPERMS"), MUTATIONS, CHECK_METRIC) == []
    )
    paired = variant("M-SQLI + I-COMMENT", "M-AUTHOFF", variant_id="VI2")
    assert static_problems(paired, MUTATIONS, CHECK_METRIC) == []


@pytest.mark.parametrize(
    ("mutations", "message"),
    [
        (("M-SECRET", "M-VULNDEP"), "both target security (rule 1)"),
        (("M-ROOT", "M-LATEST"), "both target container (rule 1)"),
        (("M-SECRET", "M-LOGSECRET"), "can't share a variant (rule 3)"),
        (("M-NOTESTS", "M-CIPERMS"), "can't be combined with ['M-CIPERMS'] (rule 4)"),
        (("M-NOPE",), "unknown mutation M-NOPE"),
        (("M-SQLI + M-AUTHOFF",), "a pair is one defect plus injections"),
    ],
)
def test_static_rule_violations(mutations: tuple[str, ...], message: str) -> None:
    problems = static_problems(variant(*mutations), MUTATIONS, CHECK_METRIC)
    assert any(message in p for p in problems), problems


def test_notests_alone_targets_two_metrics_without_conflict() -> None:
    assert static_problems(variant("M-NOTESTS"), MUTATIONS, CHECK_METRIC) == []


# --- rule 2: same file, checked at apply time --------------------------------------------------


def test_two_mutations_editing_one_file_are_rejected(tmp_path: Path) -> None:
    clash = {**MUTATIONS, "M-X": mutation("M-X", {"DOC-01": "fail"}, "app/items.py")}
    with pytest.raises(VariantError, match=r"M-SQLI and M-X both edit app/items\.py"):
        apply_variant(variant("M-SQLI", "M-X"), tmp_path, clash, seed=1)


def test_a_declared_injection_pair_may_share_its_file(tmp_path: Path) -> None:
    applied = apply_variant(variant("M-SQLI + I-COMMENT"), tmp_path, MUTATIONS, seed=1)
    assert [a.mutation.id for a in applied] == ["M-SQLI", "I-COMMENT"]
    assert (tmp_path / "app/items.py").read_text() == "# M-SQLI\n# I-COMMENT\n"


def test_unpaired_injection_on_the_same_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(VariantError, match="rule 2"):
        apply_variant(variant("M-SQLI", "I-COMMENT"), tmp_path, MUTATIONS, seed=1)


# --- plan and projection -----------------------------------------------------------------------

REPOS = [
    EvalRepo(
        id="primary", url="https://github.com/o/p", commit="a" * 40, license="MIT", langs=["python"]
    ),
    EvalRepo(
        id="cross", url="https://github.com/o/c", commit="b" * 40, license="MIT", langs=["csharp"]
    ),
]


def config(*suite: SuiteEntry, features: Features | None = None) -> EvalConfig:
    return EvalConfig(name="t", budget_usd=2.0, suite=list(suite), features=features or Features())


def plan_of(cfg: EvalConfig, *variants: VariantSpec):
    return build_plan(
        cfg, repos=REPOS, variants=list(variants), mutations=MUTATIONS, rubrics=RUBRICS
    )


def test_plan_expands_runs_with_cache_flags() -> None:
    plan = plan_of(
        config(SuiteEntry(repo="primary", runs=3), SuiteEntry(repo="primary", variant="V1")),
        variant("M-SECRET", "M-ROOT"),
    )
    assert plan.problems == []
    assert [(r.name, r.use_cache) for r in plan.runs] == [
        ("primary.base.0", False), ("primary.base.1", False), ("primary.base.2", False),
        ("primary.V1.0", True),
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("suite", "variants", "message"),
    [
        ([SuiteEntry(repo="nope")], [], "unknown repo 'nope'"),
        ([SuiteEntry(repo="primary", variant="V9")], [], "unknown variant 'V9'"),
        (
            [SuiteEntry(repo="cross", variant="V1")],
            [variant("M-ROOT")],
            "is for primary, not cross",
        ),
        ([SuiteEntry(repo="primary"), SuiteEntry(repo="primary")], [], "listed twice"),
        ([SuiteEntry(repo="primary", variant="V1")], [variant("M-SECRET", "M-VULNDEP")], "rule 1"),
    ],
)
def test_plan_problems(suite: list[SuiteEntry], variants: list[VariantSpec], message: str) -> None:
    plan = plan_of(config(*suite), *variants)
    assert any(message in p for p in plan.problems), plan.problems


def test_ablation_switches_wait_for_m51() -> None:
    plan = plan_of(config(SuiteEntry(repo="primary"), features=Features(self_consistency=False)))
    assert plan.problems == ["feature not supported yet (M5.1): self_consistency: false"]
    ok = plan_of(
        config(SuiteEntry(repo="primary"), features=Features(verifier="mechanical", skeptic=False))
    )
    assert ok.problems == []  # the verifier switches already exist


def test_projection_with_zero_mutations() -> None:
    plan = plan_of(config(SuiteEntry(repo="primary", runs=2), SuiteEntry(repo="cross")))
    projection = project(plan, repo_config(), RUBRICS)
    assert projection.runs == 3 and projection.per_run > 0
    assert projection.total == projection.per_run * 3 and projection.budget == Decimal("2.0")
