"""Deterministic scoring (docs/RUBRICS.md §4). Pure functions of (rubrics, findings, profile).

Only `scored` findings (verified, verdict pass/partial/fail) move a score. All arithmetic is
`Decimal` built from the weights' string form; checks are visited in `check_id` order, so equal
inputs give bit-for-bit equal outputs whatever the order of the findings. Scores are rounded with
`ROUND_HALF_UP` to one decimal and become floats only in the returned models.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from archlens.models import (
    CheckSpec,
    Finding,
    MetricScore,
    RepoProfile,
    Rubric,
    Severity,
    Verdict,
)

SEVERITY_WEIGHTS: dict[Severity, Decimal] = {
    "critical": Decimal(5),
    "high": Decimal(3),
    "medium": Decimal(2),
    "low": Decimal(1),
    "info": Decimal(0),
}
VALUES: dict[Verdict, Decimal] = {"pass": Decimal(1), "partial": Decimal("0.5"), "fail": Decimal(0)}
COVERAGE_GATE = Decimal("0.6")
CRITICAL_FAIL_CAP = Decimal("4.0")
CRITICAL_PARTIAL_CAP = Decimal("6.0")
MIN_METRICS_FOR_OVERALL = 5
TEN = Decimal(10)
ONE_DECIMAL = Decimal("0.1")
VERDICTS: tuple[Verdict, ...] = ("pass", "partial", "fail", "not_applicable", "unknown")
GRADES: tuple[tuple[Decimal, str], ...] = (
    (Decimal("8.5"), "A"),
    (Decimal("7.0"), "B"),
    (Decimal("5.5"), "C"),
    (Decimal("4.0"), "D"),
)


@dataclass(frozen=True)
class Scores:
    metric_scores: list[MetricScore]  # sorted by metric
    overall: float | None


def check_weight(check: CheckSpec) -> Decimal:
    """The check's `weight`, else the default for its severity."""
    if check.weight is not None:
        return Decimal(str(check.weight))
    return SEVERITY_WEIGHTS[check.severity]


def round_score(value: Decimal) -> Decimal:
    return value.quantize(ONE_DECIMAL, rounding=ROUND_HALF_UP)


def grade(score: float | None) -> str | None:
    """Report-only grade band: A ≥ 8.5, B ≥ 7.0, C ≥ 5.5, D ≥ 4.0, E below."""
    if score is None:
        return None
    value = Decimal(str(score))
    return next((letter for floor, letter in GRADES if value >= floor), "E")


def score_metric(
    rubric: Rubric, findings: Iterable[Finding], flags: Mapping[str, bool]
) -> MetricScore:
    """RUBRICS.md §4 steps 1-6 for one metric. Findings of other metrics, retired or
    non-applicable checks are ignored. Raises ValueError for two findings of one check."""
    by_check = _by_check(rubric.metric, findings)
    applicable = sorted(
        (c for c in rubric.checks if not c.retired and c.applies_when.holds(flags)),
        key=lambda c: c.id,
    )
    counts = dict.fromkeys(VERDICTS, 0)
    if not rubric.applies_when.holds(flags):
        return MetricScore(
            metric=rubric.metric, status="not_applicable", score=None, coverage=0.0,
            capped_by=[], counts=counts,
        )  # fmt: skip
    total = Decimal(0)  # Σ_A w
    scored_weight = Decimal(0)  # Σ_S w
    weighted = Decimal(0)  # Σ_S w·v
    critical_fails: list[str] = []
    critical_partials: list[str] = []
    for check in applicable:
        finding = by_check.get(check.id)
        verdict = _effective_verdict(finding)
        counts[verdict] += 1
        if verdict == "not_applicable":
            continue  # a verified NA leaves A
        weight = check_weight(check)
        total += weight
        if verdict in VALUES:
            scored_weight += weight
            weighted += weight * VALUES[verdict]
            if check.severity == "critical" and verdict == "fail":
                critical_fails.append(check.id)
            elif check.severity == "critical" and verdict == "partial":
                critical_partials.append(check.id)
    if total == 0:
        return MetricScore(
            metric=rubric.metric, status="not_applicable", score=None, coverage=0.0,
            capped_by=[], counts=counts,
        )  # fmt: skip
    coverage = scored_weight / total
    raw = TEN * weighted / scored_weight if scored_weight > 0 else None
    cap, capped_by = None, list[str]()
    if critical_fails:
        cap, capped_by = CRITICAL_FAIL_CAP, critical_fails
    elif critical_partials:
        cap, capped_by = CRITICAL_PARTIAL_CAP, critical_partials
    if raw is not None and cap is not None:
        score: Decimal | None = min(raw, cap)
    elif raw is not None and coverage >= COVERAGE_GATE:
        score = raw
    else:
        score, capped_by = None, []  # all scored weights are 0: there is nothing to cap
    return MetricScore(
        metric=rubric.metric,
        status="scored" if score is not None else "insufficient_evidence",
        score=float(round_score(score)) if score is not None else None,
        coverage=float(coverage),
        capped_by=capped_by,
        counts=counts,
    )


def score_assessment(
    rubrics: Mapping[str, Rubric], findings: Sequence[Finding], profile: RepoProfile
) -> Scores:
    """Every metric's score (sorted by metric) and the overall score: the weighted mean of the
    `scored` metrics' rounded scores by metric `weight`, rounded the same way; None when fewer
    than 5 metrics are scored (or their weights sum to 0)."""
    metric_scores = [
        score_metric(rubrics[name], findings, profile.flags) for name in sorted(rubrics)
    ]
    scored = [m for m in metric_scores if m.score is not None]
    overall: float | None = None
    if len(scored) >= MIN_METRICS_FOR_OVERALL:
        weights = {m.metric: Decimal(str(rubrics[m.metric].weight)) for m in scored}
        total = sum(weights.values(), Decimal(0))
        if total > 0:
            mean = (
                sum((weights[m.metric] * Decimal(str(m.score)) for m in scored), Decimal(0)) / total
            )
            overall = float(round_score(mean))
    return Scores(metric_scores, overall)


def _effective_verdict(finding: Finding | None) -> Verdict:
    """scored → its verdict; verified NA → not_applicable; anything else → unknown."""
    if finding is None:
        return "unknown"
    if finding.scored:
        return finding.result.verdict
    verified_na = (
        finding.result.verdict == "not_applicable" and finding.verification.status == "verified"
    )
    return "not_applicable" if verified_na else "unknown"


def _by_check(metric: str, findings: Iterable[Finding]) -> dict[str, Finding]:
    by_check: dict[str, Finding] = {}
    for finding in findings:
        if finding.result.metric != metric:
            continue
        check_id = finding.result.check_id
        if check_id in by_check:
            raise ValueError(f"{metric}: more than one finding for {check_id}")
        by_check[check_id] = finding
    return by_check
