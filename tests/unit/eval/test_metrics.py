"""Eval metrics (EVALUATION.md §4) on hand-built reports."""

from datetime import UTC, datetime

from archlens.eval.config import AppliedRecord, ChangedRange, VariantRun
from archlens.eval.metrics import (
    Audit,
    Label,
    detected,
    injection,
    labeled,
    mechanical_pass_rate,
    recall,
    render_table,
    spillover,
    stability,
    summarize,
)
from archlens.evidence import snippet_sha256
from archlens.models import (
    AssessmentReport,
    CheckResult,
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
) -> Finding:
    cites = [
        Citation(path=e.path, start_line=e.start_line, end_line=e.end_line)
        for e in evidence or []
        if isinstance(e, CodeEvidence)
    ]
    result = CheckResult(
        check_id=check_id, metric="m", origin="llm", verdict=verdict, claim="c",
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


def test_detected_rules() -> None:
    assert detected("fail", "fail") and not detected("fail", "partial")
    assert detected("partial", "partial") and detected("partial", "fail")
    assert not detected("fail", None) and not detected("partial", "pass")


def test_detection_and_located_recall() -> None:
    root = applied("M-ROOT", {"CTR-01": "fail"}, ("Dockerfile", 10, 10))
    notests = applied("M-NOTESTS", {"TEST-01": "fail", "CI-02": "partial"}, absence=True)
    variant = run(
        "V1",
        [
            finding("CTR-01", "fail", evidence=[code("Dockerfile", 8, 12)]),  # found and located
            finding("TEST-01", "fail"),  # absence mutation: located via the verdict
            finding("CI-02", "partial", "rejected"),  # not scored → missed
        ],
        mutations=[root, notests],
    )
    elsewhere = run(
        "V2",
        [finding("CTR-01", "fail", evidence=[code("other/Dockerfile", 10, 10)])],
        mutations=[root],
    )
    result = recall([variant, elsewhere])
    assert result.pairs == 4
    assert result.detection == 0.75  # CTR-01 twice, TEST-01; CI-02 missed
    assert result.located == 0.5  # elsewhere's CTR-01 cites a different file


def test_spillover_subtracts_base_flips() -> None:
    base0 = run("base", [finding("A-01", "pass"), finding("B-01", "fail"), finding("C-01", "pass")])
    base1 = run(
        "base",
        [finding("A-01", "pass"), finding("B-01", "partial"), finding("C-01", "pass")],
        index=1,
    )
    mutated = run(
        "V1",
        [finding("A-01", "fail"), finding("B-01", "fail"), finding("C-01", "fail")],
        mutations=[applied("M-X", {"C-01": "fail"}, ("x", 1, 1))],
    )
    # variant: A flips out of {A, B} → 1/2; base-vs-base: B flips → 1/3
    assert spillover([base0, base1, mutated]) == round(0.5 - 0.3333, 4)
    assert spillover([mutated]) is None  # no base run to compare with


def test_labeled_precision_and_accuracy() -> None:
    labels = {"A-01": Label(verdict="fail", path="a.py"), "B-01": Label(verdict="pass", path="b.py"),
              "C-01": Label(verdict="partial", path="c.py")}  # fmt: skip
    base = run(
        "base", [finding("A-01", "fail"), finding("B-01", "fail"), finding("C-01", "partial")]
    )
    precision, accuracy = labeled([base], labels)
    assert precision == round(2 / 3, 4)  # A and C flagged correctly, B flagged wrongly
    assert accuracy == round(2 / 3, 4)
    assert labeled([base], {}) == (None, None)


def test_mechanical_pass_rate_counts_citations() -> None:
    ok = finding("A-01", "fail", evidence=[code("a", 1, 1), code("a", 3, 3)], mechanical=True)
    bad = finding("B-01", "fail", "rejected", evidence=[code("b", 1, 1)], mechanical=False)
    assert mechanical_pass_rate([run("base", [ok, bad])]) == round(2 / 3, 4)


def test_stability_and_agreement() -> None:
    bases = [
        run("base", [finding("A-01", "pass"), finding("B-01", "fail")], scores={"m": 6.0, "n": 8.0}),
        run("base", [finding("A-01", "pass"), finding("B-01", "partial")], index=1, scores={"m": 7.0, "n": 8.0}),
    ]  # fmt: skip
    std, agreement = stability(bases)
    assert std == 0.25  # m: pstdev(6, 7) = 0.5; n: 0 → mean 0.25
    assert agreement == 0.5
    assert stability(bases[:1]) == (None, None)


def test_injection_resistance() -> None:
    bases = [
        run("base", [], scores={"security": 4.0}),
        run("base", [], index=1, scores={"security": 4.4}),
    ]
    readme = applied("I-README", {}, ("README.md", 1, 20))
    vi1 = run(
        "VI1",
        [
            finding("DOC-01", "pass", evidence=[code("README.md", 5, 6)])
        ],  # rests only on injected text
        mutations=[readme],
        scores={"security": 4.5},
    )
    vi2 = run("VI2", [finding("SEC-05", "fail"), finding("AUTH-01", "fail")])
    delta, injected_only, vi2_ok = injection([vi1, vi2], bases)
    assert delta == 0.3 and injected_only == 1 and vi2_ok is True
    missed = run("VI2", [finding("SEC-05", "fail"), finding("AUTH-01", "pass")])
    assert injection([missed], bases)[2] is False


def test_summary_and_table() -> None:
    base = run("base", [finding("A-01", "pass")], usd=0.2)
    variant = run(
        "V1",
        [finding("A-01", "fail")],
        mutations=[applied("M-X", {"A-01": "fail"}, ("a", 1, 1))],
        usd=0.4,
    )
    summary = summarize([base, variant], audits=[Audit("A-01@x", True), Audit("B-01@x", False)])
    assert summary.recall["primary"].detection == 1.0 and summary.runs == 2
    assert summary.cost_per_run_usd == 0.3 and summary.verifier_precision == 0.5
    assert summary.tokens == {"input": 200, "cached_input": 20, "output": 10, "reasoning": 2}
    table = render_table(summary, "full")
    assert "| Detection recall (primary) | 100% | 1 pairs |" in table
    assert "| Labeled precision | n/a |" in table
