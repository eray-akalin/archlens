"""View model shared by the Markdown and HTML renderers.

Everything shown comes from the `AssessmentReport`; rubrics are optional and only add check
titles and severities. Repository-derived text (claims, snippets, paths) is passed through
untouched here and escaped by each renderer for its format.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from archlens.models import (
    AssessmentReport,
    CodeEvidence,
    Finding,
    MetricScore,
    Rubric,
    ScanEvidence,
)
from archlens.report.synth import coverage_percent
from archlens.score import grade

LANGUAGES = {
    ".py": "python", ".js": "javascript", ".jsx": "jsx", ".ts": "typescript", ".tsx": "tsx",
    ".java": "java", ".kt": "kotlin", ".cs": "csharp", ".go": "go", ".rb": "ruby", ".php": "php",
    ".rs": "rust", ".yml": "yaml", ".yaml": "yaml", ".json": "json", ".toml": "toml",
    ".sh": "bash", ".tf": "hcl", ".sql": "sql", ".md": "markdown", ".html": "html",
}  # fmt: skip
VERDICT_COLUMNS = ("pass", "partial", "fail", "not_applicable", "unknown")


@dataclass(frozen=True)
class CodeView:
    path: str
    start: int
    end: int
    snippet: str
    language: str


@dataclass(frozen=True)
class FindingView:
    id: str
    check_id: str
    metric: str
    title: str
    severity: str
    verdict: str
    claim: str
    status: str
    why: str  # reason / last verification detail (other findings)
    code: list[CodeView] = field(default_factory=list[CodeView])
    scans: list[str] = field(default_factory=list[str])


@dataclass(frozen=True)
class MetricView:
    metric: str
    title: str
    status: str
    score: str
    grade: str
    coverage: int
    counts: list[int]
    capped_by: list[str]
    narrative: str
    findings: list[FindingView]


def build_view(
    report: AssessmentReport, rubrics: Mapping[str, Rubric] | None = None
) -> dict[str, object]:
    rubrics = rubrics or {}
    checks = {(r.metric, c.id): c for r in rubrics.values() for c in r.checks}
    narrative = report.narrative
    paragraphs = {m.metric: m.text for m in narrative.per_metric} if narrative else {}

    def finding_view(f: Finding) -> FindingView:
        spec = checks.get((f.result.metric, f.result.check_id))
        steps = f.verification.steps
        why = f.result.reason or (steps[-1].detail if steps else "")
        code = [
            CodeView(e.path, e.start_line, e.end_line, e.snippet, _language(e.path))
            for e in f.result.evidence
            if isinstance(e, CodeEvidence)
        ]
        scans = [
            f"{e.tool} {e.tool_version}: {e.query} → {e.result_count}"
            for e in f.result.evidence
            if isinstance(e, ScanEvidence)
        ]
        return FindingView(
            id=f.id,
            check_id=f.result.check_id,
            metric=f.result.metric,
            title=spec.title if spec else "",
            severity=spec.severity if spec else "",
            verdict=f.result.verdict,
            claim=f.result.claim,
            status=f.verification.status,
            why=why,
            code=code,
            scans=scans,
        )

    def metric_view(m: MetricScore) -> MetricView:
        rubric = rubrics.get(m.metric)
        return MetricView(
            metric=m.metric,
            title=rubric.title if rubric else m.metric,
            status=m.status,
            score=f"{m.score:.1f}" if m.score is not None else "-",
            grade=grade(m.score) or "-",
            coverage=coverage_percent(m.coverage),
            counts=[m.counts.get(v, 0) for v in VERDICT_COLUMNS],
            capped_by=m.capped_by,
            narrative=paragraphs.get(m.metric, ""),
            findings=[finding_view(f) for f in report.findings if f.result.metric == m.metric],
        )

    overall = report.overall_score
    return {
        "report": report,
        "repo": report.repo_url or "(local path)",
        "commit": report.commit_sha[:12],
        "created": report.created_at.isoformat(timespec="seconds"),
        "overall": f"{overall:.1f}" if overall is not None else None,
        "overall_grade": grade(overall),
        "summary": narrative.executive_summary if narrative else "",
        "metrics": [metric_view(m) for m in report.metric_scores],
        "other": [finding_view(f) for f in report.other_findings],
        "cost": report.cost,
        "tool_runs": sorted(report.tool_runs, key=lambda r: r.tool),
        "timings": sorted(report.timings_ms.items()),
    }


def _language(path: str) -> str:
    return LANGUAGES.get(PurePosixPath(path).suffix.lower(), "")
