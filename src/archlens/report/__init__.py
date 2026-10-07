"""Stage 7: report — `AssessmentReport`, narrative, Markdown and HTML (ARCHITECTURE.md §2.7)."""

from archlens.report.builder import build_report, config_fingerprint, cost_summary, with_narrative
from archlens.report.render import render_html, render_markdown
from archlens.report.synth import SynthPrompts, SynthResult, synthesize

__all__ = [
    "SynthPrompts",
    "SynthResult",
    "build_report",
    "config_fingerprint",
    "cost_summary",
    "render_html",
    "render_markdown",
    "synthesize",
    "with_narrative",
]
