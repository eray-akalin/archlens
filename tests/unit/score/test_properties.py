"""Property tests: the scorer is a deterministic, order-independent function (RUBRICS.md §4)."""

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from archlens.models import CheckType, Finding, Rubric, Severity, Verdict, VerificationStatus
from archlens.score import score_assessment, score_metric
from tests.unit.score.helpers import check, finding, profile, rubric

SEVERITIES: list[Severity] = ["critical", "high", "medium", "low", "info"]
VERDICTS: list[Verdict] = ["pass", "partial", "fail", "not_applicable", "unknown"]
STATUSES: list[VerificationStatus] = ["verified", "unverified", "rejected", "disputed"]
ORIGINS: list[CheckType] = ["llm", "deterministic"]
CONDITIONS: list[dict[str, list[str]]] = [{}, {"any": ["has_ci"]}, {"none": ["has_tests"]}]
Flags = dict[str, bool]


@st.composite
def scenarios(draw: st.DrawFn, metric: str = "demo") -> tuple[Rubric, list[Finding], Flags]:
    checks = []
    findings: list[Finding] = []
    prefix = metric.upper().replace("_", "")[:4]
    for i in range(draw(st.integers(min_value=0, max_value=8))):
        check_id = f"{prefix}-{i:02d}"
        checks.append(
            check(
                check_id,
                draw(st.sampled_from(SEVERITIES)),
                weight=draw(st.none() | st.sampled_from([0.0, 0.5, 1.0, 1.5, 4.0])),
                applies_when=draw(st.sampled_from(CONDITIONS)),
                retired=draw(st.booleans()) and draw(st.booleans()),
            )
        )
        if draw(st.booleans()):
            findings.append(
                finding(
                    check_id,
                    draw(st.sampled_from(VERDICTS)),
                    draw(st.sampled_from(STATUSES)),
                    metric=metric,
                    origin=draw(st.sampled_from(ORIGINS)),
                )
            )
    flags = {"has_ci": draw(st.booleans()), "has_tests": draw(st.booleans())}
    return rubric(*checks, metric=metric), findings, flags


@settings(max_examples=300, deadline=None)
@given(st.data())
def test_order_invariant_and_deterministic(data: st.DataObject) -> None:
    demo, findings, flags = data.draw(scenarios())
    shuffled = data.draw(st.permutations(findings))
    first = score_metric(demo, findings, flags)
    assert score_metric(demo, shuffled, flags).model_dump_json() == first.model_dump_json()
    assert score_metric(demo, list(findings), flags).model_dump_json() == first.model_dump_json()


@settings(max_examples=300, deadline=None)
@given(scenarios())
def test_invariants(scenario: tuple[Rubric, list[Finding], Flags]) -> None:
    demo, findings, flags = scenario
    result = score_metric(demo, findings, flags)
    applicable = [c for c in demo.checks if not c.retired and c.applies_when.holds(flags)]
    assert sum(result.counts.values()) == len(applicable)
    assert 0.0 <= result.coverage <= 1.0
    if result.status == "scored":
        assert result.score is not None and 0.0 <= result.score <= 10.0
        assert Decimal(str(result.score)) == Decimal(str(result.score)).quantize(Decimal("0.1"))
        if not result.capped_by:
            assert result.coverage >= 0.6
    else:
        assert result.score is None and result.capped_by == []
    by_id = {f.result.check_id: f for f in findings}
    critical_fails = sorted(
        c.id
        for c in applicable
        if c.severity == "critical"
        and (f := by_id.get(c.id)) is not None
        and f.scored
        and f.result.verdict == "fail"
    )
    if critical_fails and result.status == "scored":
        assert result.score is not None and result.score <= 4.0
        assert result.capped_by == critical_fails
    if critical_fails and any(
        (c.weight is None or c.weight > 0) and c.id in critical_fails for c in applicable
    ):
        assert result.status == "scored"  # a weighted critical fail always produces a score


@settings(max_examples=300, deadline=None)
@given(st.data())
def test_upgrading_a_fail_never_lowers_a_score(data: st.DataObject) -> None:
    demo, findings, flags = data.draw(scenarios())
    fails = [i for i, f in enumerate(findings) if f.scored and f.result.verdict == "fail"]
    if not fails:
        return
    index = data.draw(st.sampled_from(fails))
    before = score_metric(demo, findings, flags)
    upgraded = list(findings)
    old = findings[index]
    upgraded[index] = finding(old.result.check_id, "pass", origin=old.result.origin)
    after = score_metric(demo, upgraded, flags)
    if before.score is not None and after.score is not None:
        assert after.score >= before.score


@settings(max_examples=100, deadline=None)
@given(st.data())
def test_overall_is_independent_of_metric_order(data: st.DataObject) -> None:
    names = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"]
    drawn = [data.draw(scenarios(metric=name)) for name in names]
    rubrics = {r.metric: r for r, _, _ in drawn}
    findings = [f for _, fs, _ in drawn for f in fs]
    flags = drawn[0][2]
    reordered = dict(data.draw(st.permutations(list(rubrics.items()))))
    shuffled = data.draw(st.permutations(findings))
    first = score_assessment(rubrics, findings, profile(**flags))
    second = score_assessment(reordered, shuffled, profile(**flags))
    assert first == second
    assert [m.metric for m in first.metric_scores] == sorted(names)
    if first.overall is not None:
        assert sum(m.status == "scored" for m in first.metric_scores) >= 5
