"""View model shared by the Markdown and HTML renderers.

Everything shown comes from the `AssessmentReport`; rubrics are optional and only add check
titles and severities. Repository-derived text (claims, snippets, paths) is passed through
untouched here and escaped by each renderer for its format.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import quote

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
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
_GITHUB_REPO = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?")
_FULL_SHA = re.compile(r"[0-9a-f]{40}")


def finding_anchor(check_id: str) -> str:
    return f"f-{check_id.lower()}"


def blob_base(repo_url: str | None, commit_sha: str) -> str:
    """`https://github.com/<owner>/<repo>/blob/<sha>/` for a GitHub repo at a real commit; ""
    otherwise (other hosts, local paths, whose commit is a content ID)."""
    if not repo_url or not _FULL_SHA.fullmatch(commit_sha):
        return ""
    url = repo_url.removesuffix("/").removesuffix(".git")
    return f"{url}/blob/{commit_sha}/" if _GITHUB_REPO.fullmatch(url) else ""


@dataclass(frozen=True)
class CodeView:
    path: str
    start: int
    end: int
    snippet: str
    language: str
    url: str = ""  # the lines on GitHub at the assessed commit; "" for other hosts and local paths

    @property
    def lines(self) -> list[tuple[int, str]]:
        """(line number, text) for each snippet line (HTML shows the numbers in a gutter)."""
        return list(enumerate(self.snippet.split("\n"), start=self.start))


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

    @property
    def anchor(self) -> str:
        return finding_anchor(self.check_id)

    @property
    def is_issue(self) -> bool:
        return self.verdict in ("fail", "partial")


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
    value: float | None = None  # the score as a number (HTML score bar)

    @property
    def anchor(self) -> str:
        return f"m-{self.metric}"

    @property
    def issues(self) -> list[FindingView]:
        """fail / partial findings, most severe first."""
        found = [f for f in self.findings if f.is_issue]
        return sorted(found, key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.check_id))

    @property
    def passes(self) -> list[FindingView]:
        return [f for f in self.findings if not f.is_issue]


def build_view(
    report: AssessmentReport, rubrics: Mapping[str, Rubric] | None = None
) -> dict[str, object]:
    rubrics = rubrics or {}
    checks = {(r.metric, c.id): c for r in rubrics.values() for c in r.checks}
    blob = blob_base(report.repo_url, report.commit_sha)

    def link(path: str, start: int, end: int) -> str:
        return f"{blob}{quote(path, safe='/')}#L{start}-L{end}" if blob else ""

    narrative = report.narrative
    paragraphs = {m.metric: m.text for m in narrative.per_metric} if narrative else {}

    def finding_view(f: Finding) -> FindingView:
        spec = checks.get((f.result.metric, f.result.check_id))
        steps = f.verification.steps
        why = f.result.reason or (steps[-1].detail if steps else "")
        code = [
            CodeView(
                e.path,
                e.start_line,
                e.end_line,
                e.snippet,
                _language(e.path),
                link(e.path, e.start_line, e.end_line),
            )
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
            value=m.score,
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
        "finding_ids": {f.id for f in report.findings},
    }


def _language(path: str) -> str:
    return LANGUAGES.get(PurePosixPath(path).suffix.lower(), "")
