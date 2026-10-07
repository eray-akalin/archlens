"""Hand-built eval run records for metric, label and audit tests."""

from datetime import UTC, datetime

from archlens.eval.config import AppliedRecord, ChangedRange, VariantRun
from archlens.evidence import snippet_sha256
from archlens.models import (
    AssessmentReport,
    CheckResult,
    CheckType,
    Citation,
    CodeEvidence,
    ConfigFingerprint,
    CostSummary,
    Evidence,
    Finding,
    MetricScore,
    RepoProfile,
    Verdict,
    Verification,
    VerificationStatus,
    VerificationStep,
    is_scorable,
)

PROFILE = RepoProfile(
    languages={}, frameworks=[], package_managers=[], ci_systems=[], test_frameworks=[], flags={},
    entrypoints=[],
)  # fmt: skip
CONFIG = ConfigFingerprint(
    archlens_version="0", rubric_versions={}, prompt_versions={}, models={}, tool_versions={}
)


def code(path: str, start: int, end: int) -> CodeEvidence:
    return CodeEvidence(
        path=path, start_line=start, end_line=end, snippet="x", snippet_sha256=snippet_sha256("x")
    )


def finding(
    check_id: str,
    verdict: Verdict,
    status: VerificationStatus = "verified",
    *,
    evidence: list[Evidence] | None = None,
    mechanical: bool | None = None,
    origin: CheckType = "llm",
    claim: str = "c",
) -> Finding:
    cites = [
        Citation(path=e.path, start_line=e.start_line, end_line=e.end_line)
        for e in evidence or []
        if isinstance(e, CodeEvidence)
    ]
    result = CheckResult(
        check_id=check_id, metric="m", origin=origin, verdict=verdict, claim=claim,
        citations=cites, evidence=evidence or [], confidence="high", rubric_version="1",
    )  # fmt: skip
    steps = (
        []
        if mechanical is None
        else [VerificationStep(step="mechanical", passed=mechanical, detail="")]
    )
    verification = Verification(status=status, steps=steps)
    return Finding(
        id=f"{check_id}@x",
        result=result,
        verification=verification,
        scored=is_scorable(result, verification),
    )


def run(
    variant: str,
    findings: list[Finding],
    *,
    index: int = 0,
    repo: str = "primary",
    mutations: list[AppliedRecord] | None = None,
    scores: dict[str, float] | None = None,
    usd: float = 0.1,
) -> VariantRun:
    report = AssessmentReport(
        run_id="r", repo_url=None, commit_sha="a" * 40, created_at=datetime(2026, 1, 1, tzinfo=UTC),
        config=CONFIG, profile=PROFILE,
        metric_scores=[
            MetricScore(metric=m, status="scored", score=s, coverage=1.0, capped_by=[], counts={})
            for m, s in (scores or {}).items()
        ],
        overall_score=None,
        findings=[f for f in findings if f.scored], other_findings=[f for f in findings if not f.scored],
        tool_runs=[],
        cost=CostSummary(input_tokens=100, cached_input_tokens=10, output_tokens=5, reasoning_tokens=1,
                         usd=usd, by_stage={}, by_metric={}),
        timings_ms={}, narrative=None,
    )  # fmt: skip
    return VariantRun(
        name=f"{repo}.{variant}.{index}", repo=repo, variant=variant, index=index, commit="a+v",
        mutations=mutations or [], skipped=[], seconds=60.0, report=report,
    )  # fmt: skip


def applied(
    mutation_id: str,
    expected: dict[str, Verdict],
    *changed: tuple[str, int, int],
    absence: bool = False,
    may_affect: tuple[str, ...] = (),
) -> AppliedRecord:
    return AppliedRecord(
        id=mutation_id, expected=expected, may_affect=list(may_affect), absence=absence,
        injection=mutation_id.startswith("I-"),
        changed=[ChangedRange(path=p, start_line=a, end_line=b) for p, a, b in changed],
    )  # fmt: skip
