"""`pr_comment.md` (ARCHITECTURE.md §2.7): what a change did to the assessment, against a base
report — overall and per-metric score deltas, and the verified findings that appeared, went away
or changed verdict. Findings are matched by check ID (one finding per check per run). Every number
comes from the two reports; the deltas are plain subtraction.

The comment is posted publicly, so repository-derived text is inline-escaped and cannot mention
users, link, or embed images.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from archlens.models import AssessmentReport, CheckSpec, CodeEvidence, Finding, MetricScore, Rubric
from archlens.report.render import md_text

MARKER = "<!-- archlens:pr-comment -->"
ISSUES = ("fail", "partial")
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def comment_text(value: object) -> str:
    """`md_text`, plus no @mentions, links or images."""
    text = md_text(value).replace("@", "@​")
    return text.replace("[", "\\[").replace("]", "\\]").replace("!", "\\!")


@dataclass(frozen=True)
class _Change:
    check_id: str
    title: str
    severity: str
    base: str  # verdict, or "—"
    head: str
    claim: str
    location: str


def render_pr_comment(
    base: AssessmentReport,
    head: AssessmentReport,
    rubrics: Mapping[str, Rubric],
    reused: Sequence[str] = (),
) -> str:
    """Markdown for a PR comment comparing `head` with `base`. `reused` names the metrics whose
    LLM results came from the metric result cache in the head run."""
    specs = {c.id: c for r in rubrics.values() for c in r.checks}
    titles = {r.metric: r.title for r in rubrics.values()}
    lines = [MARKER, f"## ArchLens: {_overall(base.overall_score, head.overall_score)}", ""]
    evaluated = [m.metric for m in head.metric_scores]
    judged = {f.result.metric for f in [*head.findings, *head.other_findings]
              if f.result.origin == "llm"}  # fmt: skip
    rerun = sorted(judged - set(reused))
    lines.append(
        f"Base `{base.commit_sha[:12]}` → head `{head.commit_sha[:12]}` · LLM re-evaluated "
        f"{len(rerun)} of {len(judged | set(reused))} metrics with LLM checks"
        + (f" (unchanged inputs, reused: {', '.join(sorted(reused))})" if reused else "")
        + f" · LLM cost ${head.cost.usd:.4f}"
    )
    lines += ["", "| Metric | Base | Head | Change |", "|---|---:|---:|---:|"]
    base_scores = {m.metric: m for m in base.metric_scores}
    head_scores = {m.metric: m for m in head.metric_scores}
    for metric in [*evaluated, *(m for m in base_scores if m not in head_scores)]:
        before, after = base_scores.get(metric), head_scores.get(metric)
        lines.append(
            f"| {md_text(titles.get(metric, metric))} | {_shown(before)} | {_shown(after)} | "
            f"{_delta(_score(before), _score(after))} |"
        )
    new, resolved, changed = _changes(base, head, specs)
    for heading, changes in (("New issues", new), ("Resolved", resolved), ("Changed", changed)):
        if changes:
            lines += ["", f"### {heading} ({len(changes)})", ""]
            lines += [_line(c) for c in changes]
    if not (new or resolved or changed):
        lines += ["", "No verified finding changed."]
    if base.config.rubric_versions != head.config.rubric_versions:
        lines += ["", "_The base report used other rubric versions; some changes may come from "
                      "the rubrics rather than the code._"]  # fmt: skip
    lines += [
        "",
        "<sub>Scores are computed by code from verified findings only. Full report: the "
        "workflow's artifacts and the code scanning alerts.</sub>",
        "",
    ]
    return "\n".join(lines)


def _score(metric: MetricScore | None) -> float | None:
    return metric.score if metric is not None else None


def _shown(metric: MetricScore | None) -> str:
    if metric is None:
        return "—"
    return f"{metric.score:.1f}" if metric.score is not None else metric.status.replace("_", " ")


def _delta(before: float | None, after: float | None) -> str:
    if before is None or after is None:
        return "—"
    change = round(after - before, 1)
    return "±0.0" if change == 0 else f"{change:+.1f}"


def _overall(before: float | None, after: float | None) -> str:
    shown = [f"{s:.1f}" if s is not None else "not computed" for s in (before, after)]
    change = _delta(before, after)
    return f"{shown[0]} → {shown[1]}" + (f" ({change})" if change != "—" else "")


def _changes(
    base: AssessmentReport, head: AssessmentReport, specs: Mapping[str, CheckSpec]
) -> tuple[list[_Change], list[_Change], list[_Change]]:
    before = {f.result.check_id: f for f in base.findings}
    after = {f.result.check_id: f for f in head.findings}
    new: list[_Change] = []
    resolved: list[_Change] = []
    changed: list[_Change] = []
    for check_id in sorted(before.keys() | after.keys()):
        old, cur = before.get(check_id), after.get(check_id)
        old_verdict = old.result.verdict if old else None
        cur_verdict = cur.result.verdict if cur else None
        was, now = old_verdict in ISSUES, cur_verdict in ISSUES
        if not (was or now) or old_verdict == cur_verdict:
            continue
        change = _change(check_id, specs.get(check_id), old_verdict, cur_verdict, cur or old)
        (changed if was and now else new if now else resolved).append(change)
    for group in (new, resolved, changed):
        group.sort(key=lambda c: (SEVERITY_ORDER.get(c.severity, 9), c.check_id))
    return new, resolved, changed


def _change(
    check_id: str,
    spec: CheckSpec | None,
    before: str | None,
    after: str | None,
    finding: Finding | None,
) -> _Change:
    code = [e for e in finding.result.evidence if isinstance(e, CodeEvidence)] if finding else []
    location = f"{code[0].path}:{code[0].start_line}-{code[0].end_line}" if code else ""
    return _Change(
        check_id=check_id,
        title=spec.title if spec else "",
        severity=spec.severity if spec else "",
        base=before or "not verified",
        head=after or "not verified",
        claim=finding.result.claim if finding else "",
        location=location,
    )


def _line(change: _Change) -> str:
    head = f"- **{change.check_id}**"
    if change.title:
        head += f" {md_text(change.title)}"
    if change.severity:
        head += f" ({change.severity})"
    text = f"{head}: {change.base} → **{change.head}**"
    if change.location:
        text += f" · `{change.location.replace('`', '')}`"
    if change.claim:
        text += f"<br>{comment_text(change.claim)}"
    return text
