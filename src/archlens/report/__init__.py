"""Stage 7: report — `AssessmentReport`, narrative, Markdown, HTML, SARIF and the PR comment
(ARCHITECTURE.md §2.7)."""

from archlens.report.builder import build_report, config_fingerprint, cost_summary, with_narrative
from archlens.report.pr import render_pr_comment
from archlens.report.render import render_html, render_markdown
from archlens.report.sarif import render_sarif
from archlens.report.synth import SynthPrompts, SynthResult, synthesize

__all__ = [
    "SynthPrompts",
    "SynthResult",
    "build_report",
    "config_fingerprint",
    "cost_summary",
    "render_html",
    "render_markdown",
    "render_pr_comment",
    "render_sarif",
    "synthesize",
    "with_narrative",
]
