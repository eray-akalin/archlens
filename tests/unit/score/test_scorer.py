"""Scorer against RUBRICS.md §4, case by case."""

from decimal import Decimal

import pytest

from archlens.models import CheckSpec, Finding, MetricScore, Rubric, Verdict
from archlens.rubric import load_rubrics
from archlens.score import check_weight, grade, score_assessment, score_metric
from archlens.score.scorer import round_score
from tests.unit.score.helpers import check, finding, profile, rubric


def score(
    *findings_: Finding, checks: tuple[CheckSpec, ...], flags: dict[str, bool] | None = None
) -> MetricScore:
    return score_metric(rubric(*checks), findings_, flags or {})


# --- weights and the basic formula -------------------------------------------------------------


def test_weighted_mean_over_scored_checks() -> None:
    checks = (check("X-01"), check("X-02"), check("X-03", "high"))  # weights 2, 2, 3
    result = score(
        finding("X-01", "pass"), finding("X-02", "fail"), finding("X-03", "partial"), checks=checks
    )
    assert (result.status, result.score, result.coverage) == ("scored", 5.0, 1.0)  # 10·3.5/7
    assert result.counts == {"pass": 1, "partial": 1, "fail": 1, "not_applicable": 0, "unknown": 0}
    assert result.capped_by == []


def test_weights_default_by_severity_and_can_be_overridden() -> None:
    assert [check_weight(check("X-01", s)) for s in ("critical", "high", "medium", "low", "info")] == [
        Decimal(5), Decimal(3), Decimal(2), Decimal(1), Decimal(0)
    ]  # fmt: skip
    assert check_weight(check("X-01", "low", weight=1.5)) == Decimal("1.5")
    checks = (check("X-01", "low", weight=3), check("X-02", "high", weight=1))
    assert score(finding("X-01", "pass"), finding("X-02", "fail"), checks=checks).score == 7.5


# --- NA handling -------------------------------------------------------------------------------


def test_verified_na_leaves_the_applicable_set() -> None:
    checks = (check("X-01"), check("X-02", "high"))
    deterministic_na = finding("X-02", "not_applicable", origin="deterministic")
    result = score(finding("X-01", "pass"), deterministic_na, checks=checks)
    assert (result.score, result.coverage) == (10.0, 1.0)
    assert result.counts["not_applicable"] == 1


def test_unverified_llm_na_stays_applicable_as_unknown() -> None:
    checks = (check("X-01"), check("X-02", "high"))
    unverified_na = finding("X-02", "not_applicable", "unverified")
    result = score(finding("X-01", "pass"), unverified_na, checks=checks)
    assert (result.status, result.coverage) == ("insufficient_evidence", 0.4)  # 2 of 5
    assert result.counts["unknown"] == 1 and result.counts["not_applicable"] == 0


def test_only_scored_findings_count() -> None:
    checks = tuple(check(f"X-0{i}") for i in range(1, 6))
    findings_ = [
        finding("X-01", "pass"),
        finding("X-02", "fail", "rejected"),
        finding("X-03", "fail", "unverified"),
        finding("X-04", "fail", "disputed"),
        finding("X-05", "unknown"),
    ]
    result = score(*findings_, checks=checks)
    assert (result.status, result.coverage, result.counts["unknown"]) == (
        "insufficient_evidence",
        0.2,
        4,
    )


# --- zero applicable weight --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("checks", "findings_", "flags"),
    [
        ((check("X-01", "info"),), [finding("X-01", "pass")], {}),  # only info checks
        ((check("X-01"),), [finding("X-01", "not_applicable", origin="deterministic")], {}),
        ((check("X-01", applies_when={"any": ["has_k8s"]}),), [finding("X-01", "fail")], {}),
        ((check("X-01", retired=True),), [finding("X-01", "fail")], {}),
        ((), [], {}),
    ],
)
def test_zero_applicable_weight_is_not_applicable(
    checks: tuple[CheckSpec, ...], findings_: list[Finding], flags: dict[str, bool]
) -> None:
    result = score(*findings_, checks=checks, flags=flags)
    assert (result.status, result.score, result.capped_by) == ("not_applicable", None, [])


def test_metric_level_applies_when() -> None:
    demo = rubric(check("X-01"), applies_when={"any": ["has_database"]})
    assert score_metric(demo, [finding("X-01", "fail")], {}).status == "not_applicable"
    assert score_metric(demo, [finding("X-01", "fail")], {"has_database": True}).status == "scored"


# --- coverage gate -----------------------------------------------------------------------------


def test_coverage_gate_boundary() -> None:
    checks = (check("X-01", "high"), check("X-02", "medium"))  # 3 + 2
    at_gate = score(finding("X-01", "pass"), checks=checks)  # 3/5 = 0.6
    assert (at_gate.status, at_gate.score, at_gate.coverage) == ("scored", 10.0, 0.6)
    below = score(finding("X-02", "pass"), checks=checks)  # 2/5
    assert (below.status, below.score) == ("insufficient_evidence", None)
    nothing = score(checks=checks)
    assert (nothing.status, nothing.coverage) == ("insufficient_evidence", 0.0)


# --- critical findings -------------------------------------------------------------------------


def test_critical_fail_overrides_the_gate_and_caps_at_4() -> None:
    checks = (check("C-01", "critical"), *(check(f"X-0{i}", "high") for i in range(1, 5)))
    result = score(finding("C-01", "fail"), checks=checks)  # coverage 5/17 < 0.6
    assert (result.status, result.score, result.capped_by) == ("scored", 0.0, ["C-01"])
    mostly_good = score(
        finding("C-01", "fail"), *(finding(f"X-0{i}", "pass") for i in range(1, 5)), checks=checks
    )
    assert (mostly_good.score, mostly_good.capped_by) == (4.0, ["C-01"])  # raw 7.06 → 4.0


def test_critical_partial_caps_at_6() -> None:
    checks = (check("C-01", "critical"), check("X-01", "high"))
    result = score(finding("C-01", "partial"), finding("X-01", "pass"), checks=checks)
    assert (result.score, result.capped_by) == (6.0, ["C-01"])  # raw 6.875
    low = score(finding("C-01", "partial"), finding("X-01", "fail"), checks=checks)
    assert (low.score, low.capped_by) == (3.1, ["C-01"])  # raw 3.125 stays below the cap


def test_critical_fail_takes_precedence_over_partial() -> None:
    checks = (check("C-01", "critical"), check("C-02", "critical"))
    result = score(finding("C-01", "partial"), finding("C-02", "fail"), checks=checks)
    assert (result.score, result.capped_by) == (2.5, ["C-02"])


def test_unscored_critical_fail_does_not_cap() -> None:
    checks = (check("C-01", "critical"), check("X-01", "high"))
    result = score(finding("C-01", "fail", "disputed"), finding("X-01", "pass"), checks=checks)
    assert (result.status, result.capped_by) == ("insufficient_evidence", [])


# --- rounding ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "rounded"),
    [("6.25", "6.3"), ("6.35", "6.4"), ("8.45", "8.5"), ("0.05", "0.1"), ("6.6666", "6.7")],
)
def test_round_half_up(raw: str, rounded: str) -> None:
    assert round_score(Decimal(raw)) == Decimal(rounded)


def test_half_up_in_a_metric() -> None:
    checks = (check("X-01", "high"), check("X-02"), check("X-03", "high"))  # 3, 2, 3
    result = score(
        finding("X-01", "pass"), finding("X-02", "pass"), finding("X-03", "fail"), checks=checks
    )
    assert result.score == 6.3  # raw 6.25; float round() would give 6.2


# --- overall -----------------------------------------------------------------------------------


def metrics(*verdicts: Verdict) -> tuple[dict[str, Rubric], list[Finding]]:
    """One single-check metric per verdict; metric i has weight 1 + i/2."""
    rubrics = {
        f"m_{chr(97 + i)}": rubric(
            check(f"M{chr(65 + i)}-01"), metric=f"m_{chr(97 + i)}", weight=1.0 + i / 2
        )
        for i in range(len(verdicts))
    }
    findings_ = [
        finding(f"M{chr(65 + i)}-01", v, metric=f"m_{chr(97 + i)}") for i, v in enumerate(verdicts)
    ]
    return rubrics, findings_


def test_overall_needs_five_scored_metrics() -> None:
    rubrics, findings_ = metrics("pass", "pass", "pass", "pass")
    result = score_assessment(rubrics, findings_, profile())
    assert result.overall is None
    assert [m.metric for m in result.metric_scores] == ["m_a", "m_b", "m_c", "m_d"]


def test_overall_weighted_mean_rounds_half_up() -> None:
    rubrics, findings_ = metrics("fail", "partial", "pass", "pass", "pass")
    result = score_assessment(rubrics, findings_, profile())
    assert [m.score for m in result.metric_scores] == [0.0, 5.0, 10.0, 10.0, 10.0]
    assert result.overall == 8.3  # (0·1 + 5·1.5 + 10·2 + 10·2.5 + 10·3) / 10 = 8.25


def test_overall_ignores_unscored_metrics() -> None:
    rubrics, findings_ = metrics("pass", "pass", "pass", "pass", "pass", "pass")
    findings_[5] = finding("MF-01", "fail", "rejected", metric="m_f")  # insufficient evidence
    result = score_assessment(rubrics, findings_, profile())
    assert result.metric_scores[5].status == "insufficient_evidence"
    assert result.overall == 10.0


# --- contract edges ----------------------------------------------------------------------------


def test_two_findings_for_one_check_is_an_error() -> None:
    with pytest.raises(ValueError, match="more than one finding for X-01"):
        score(finding("X-01", "pass"), finding("X-01", "fail"), checks=(check("X-01"),))


def test_findings_of_other_metrics_and_unknown_checks_are_ignored() -> None:
    result = score(
        finding("X-01", "pass"),
        finding("X-01", "fail", metric="other"),
        finding("X-99", "fail"),
        checks=(check("X-01"),),
    )
    assert (result.score, sum(result.counts.values())) == (10.0, 1)


@pytest.mark.parametrize(
    ("value", "letter"),
    [
        (9.0, "A"),
        (8.5, "A"),
        (8.4, "B"),
        (7.0, "B"),
        (5.5, "C"),
        (4.0, "D"),
        (3.9, "E"),
        (None, None),
    ],
)
def test_grades(value: float | None, letter: str | None) -> None:
    assert grade(value) == letter


def test_shipped_security_rubric_critical_secret() -> None:
    security = load_rubrics()["security"]
    findings_ = [
        finding("SEC-01", "fail", metric="security", origin="deterministic"),
        finding("SEC-02", "pass", metric="security", origin="deterministic"),
        finding("SEC-03", "pass", metric="security", origin="deterministic"),
        finding("SEC-07", "pass", metric="security", origin="deterministic"),
    ]
    result = score_metric(security, findings_, {})  # SEC-04..06 don't apply without flags
    assert (result.status, result.score, result.capped_by) == ("scored", 4.0, ["SEC-01"])


def test_zero_weight_edges() -> None:
    zero_critical = check("CA-01", "critical", weight=0.0)
    only_info = score(finding("CA-01", "fail"), checks=(zero_critical, check("X-01", "info")))
    assert only_info.status == "not_applicable"  # Σ_A w = 0 comes first
    capped = score(
        finding("CA-01", "fail"),
        finding("X-01", "pass"),
        checks=(zero_critical, check("X-01", "high")),
    )
    assert (capped.score, capped.capped_by) == (4.0, ["CA-01"])  # S holds a critical fail
    nothing = score(finding("CA-01", "fail"), checks=(zero_critical, check("X-01", "high")))
    assert (nothing.status, nothing.capped_by) == ("insufficient_evidence", [])  # Σ_S w = 0
