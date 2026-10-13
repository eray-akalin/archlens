"""Ablation rubric transforms (EVALUATION.md §6)."""

from pathlib import Path

from archlens.evaluate.ablation import ablate, judged_by_llm
from archlens.rubric import load_rubrics
from tests.unit.report.helpers import RUBRICS

RUBRIC_DIR = Path(__file__).parents[3] / "rubrics"


def test_nothing_off_is_a_no_op() -> None:
    assert ablate(RUBRICS) == RUBRICS


def test_deterministic_checks_become_llm_checks() -> None:
    real = load_rubrics(RUBRIC_DIR)
    out = ablate(real, deterministic_rules=False)
    for name, rubric in out.items():
        assert all(c.type == "llm" and c.rule is None for c in rubric.checks), name
        assert [c.id for c in rubric.checks] == [c.id for c in real[name].checks]
    sec01 = next(c for c in out["security"].checks if c.id == "SEC-01")
    assert sec01.guidance is not None and "No secrets committed" in sec01.guidance
    assert "What an automated rule would check:" in sec01.guidance  # the rule's docstring
    assert sec01.evidence_policy == "positive_required" and sec01.fact_kinds == []
    # LLM checks are left alone
    sec05 = next(c for c in out["security"].checks if c.id == "SEC-05")
    assert sec05 == next(c for c in real["security"].checks if c.id == "SEC-05")


def test_judged_by_llm_keeps_identity_and_weight() -> None:
    check = next(c for c in RUBRICS["security"].checks if c.type == "deterministic")
    converted = judged_by_llm(check)
    assert (converted.id, converted.severity, converted.weight) == (
        check.id,
        check.severity,
        check.weight,
    )


def test_self_consistency_off_means_one_run() -> None:
    out = ablate(RUBRICS, self_consistency=False)
    assert all(c.consistency_runs == 1 for r in out.values() for c in r.checks)
    assert any(c.consistency_runs == 2 for r in RUBRICS.values() for c in r.checks)
