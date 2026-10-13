"""Markdown and HTML renderings of an `AssessmentReport` (ARCHITECTURE.md §2.7).

Both use the same view model and Jinja2 templates in `report/templates/`. HTML is autoescaped.
Markdown escapes repository-derived inline text (`md` filter: `<`, `>`, `|`, newlines) and fences
each snippet with more backticks than the snippet contains, so no repository content can close a
block or inject markup.
"""

import re
from collections.abc import Collection, Mapping
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup, escape

from archlens.models import AssessmentReport, Rubric
from archlens.report.view import build_view, finding_anchor

TEMPLATES = Path(__file__).parent / "templates"
_BACKTICKS = re.compile(r"`+")
_FINDING_ID = re.compile(r"\b([A-Z][A-Z0-9]*-\d+)@[0-9a-f]{12}\b")


def md_text(value: object) -> str:
    """Inline Markdown text: one line, no raw HTML, safe inside table cells."""
    text = " ".join(str(value).split())
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "\\|")


def fence(snippet: str) -> str:
    """A backtick fence longer than any backtick run in `snippet` (at least three)."""
    longest = max((len(m) for m in _BACKTICKS.findall(snippet)), default=0)
    return "`" * max(3, longest + 1)


def link_findings(text: str, ids: Collection[str]) -> Markup:
    """HTML: `text` escaped, then each finding ID of this report linked to its finding. IDs are
    matched in the escaped text, so nothing from `text` can become markup."""

    def link(match: re.Match[str]) -> str:
        found = match.group(0)
        return f'<a href="#{finding_anchor(match.group(1))}">{found}</a>' if found in ids else found

    return Markup(_FINDING_ID.sub(link, str(escape(text))))


def _env(*, html: bool) -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        undefined=StrictUndefined,
        autoescape=html,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters.update(md=md_text, fence=fence, link_findings=link_findings)  # pyright: ignore[reportUnknownMemberType]
    return env


_MD = _env(html=False)
_HTML = _env(html=True)


def render_markdown(report: AssessmentReport, rubrics: Mapping[str, Rubric] | None = None) -> str:
    return _MD.get_template("report.md.j2").render(**build_view(report, rubrics))


def render_html(report: AssessmentReport, rubrics: Mapping[str, Rubric] | None = None) -> str:
    return _HTML.get_template("report.html.j2").render(**build_view(report, rubrics))
