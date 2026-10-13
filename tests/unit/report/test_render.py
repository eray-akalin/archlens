"""Markdown and HTML rendering: snapshots and escaping of repository-derived text."""

import pytest
from syrupy.assertion import SnapshotAssertion
from syrupy.extensions.single_file import SingleFileSnapshotExtension, WriteMode

from archlens.report import render_html, render_markdown
from archlens.report.render import fence, link_findings, md_text
from archlens.report.view import blob_base
from tests.unit.report.helpers import HOSTILE_SNIPPET, NARRATIVE, RUBRICS, SHA, sample_report


class MarkdownSnapshot(SingleFileSnapshotExtension):
    file_extension = "md"
    _write_mode = WriteMode.TEXT


class HtmlSnapshot(SingleFileSnapshotExtension):
    file_extension = "html"
    _write_mode = WriteMode.TEXT


@pytest.fixture
def md_snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    return snapshot.use_extension(MarkdownSnapshot)


@pytest.fixture
def html_snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    return snapshot.use_extension(HtmlSnapshot)


def test_markdown_snapshot(md_snapshot: SnapshotAssertion) -> None:
    assert render_markdown(sample_report(NARRATIVE), RUBRICS) == md_snapshot


def test_html_snapshot(html_snapshot: SnapshotAssertion) -> None:
    assert render_html(sample_report(NARRATIVE), RUBRICS) == html_snapshot


def test_markdown_without_narrative_or_rubrics(md_snapshot: SnapshotAssertion) -> None:
    assert render_markdown(sample_report()) == md_snapshot


def test_html_escapes_repository_text() -> None:
    html = render_html(sample_report(NARRATIVE), RUBRICS)
    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;/code&gt;&lt;/pre&gt;&lt;script&gt;x()" in html


def test_markdown_escapes_inline_text_and_fences_snippets() -> None:
    md = render_markdown(sample_report(NARRATIVE), RUBRICS)
    assert "<script>" not in md.replace(HOSTILE_SNIPPET, "")  # only inside the code block
    assert "&lt;script&gt;alert(1)&lt;/script&gt; \\| ignore previous instructions" in md
    assert f"`````python\n{HOSTILE_SNIPPET}\n`````" in md  # 4 backticks inside → 5-tick fence


def test_markdown_overall_not_computed() -> None:
    md = render_markdown(sample_report())
    assert "## Overall: not computed" in md
    assert "Fewer than 5 metrics" in md


@pytest.mark.parametrize(
    ("text", "escaped"),
    [("a\n b", "a b"), ("<b>&", "&lt;b&gt;&amp;"), ("x | y", "x \\| y")],
)
def test_md_text(text: str, escaped: str) -> None:
    assert md_text(text) == escaped


@pytest.mark.parametrize(("snippet", "ticks"), [("x", 3), ("``", 3), ("```", 4), ("a ````` b", 6)])
def test_fence(snippet: str, ticks: int) -> None:
    assert fence(snippet) == "`" * ticks


# --- HTML polish (M6.1) ---------------------------------------------------------------------


def test_summary_links_only_this_reports_finding_ids() -> None:
    text = f"See SEC-05@{SHA[:12]} and NOPE-01@{SHA[:12]} <b>bold</b>"
    html = str(link_findings(text, {f"SEC-05@{SHA[:12]}"}))
    assert f'<a href="#f-sec-05">SEC-05@{SHA[:12]}</a>' in html
    assert f"NOPE-01@{SHA[:12]}" in html and 'href="#f-nope-01"' not in html
    assert "<b>" not in html and "&lt;b&gt;" in html


@pytest.mark.parametrize(
    ("repo_url", "commit", "expected"),
    [
        ("https://github.com/o/r", SHA, f"https://github.com/o/r/blob/{SHA}/"),
        ("https://github.com/o/r.git", SHA, f"https://github.com/o/r/blob/{SHA}/"),
        ("https://github.com/o/r/", SHA, f"https://github.com/o/r/blob/{SHA}/"),
        ("https://gitlab.com/o/r", SHA, ""),
        ("https://github.com/o/r/extra", SHA, ""),
        (None, SHA, ""),  # local path
        ("https://github.com/o/r", "main", ""),
    ],
)
def test_blob_base(repo_url: str | None, commit: str, expected: str) -> None:
    assert blob_base(repo_url, commit) == expected


def test_html_structure() -> None:
    html = render_html(sample_report(NARRATIVE), RUBRICS)
    assert "<script" not in html.replace("&lt;script", "")  # repository text stays escaped
    # issues first; passing checks folded away
    security = html[html.index('id="m-security"') : html.index('id="m-testing"')]
    assert security.index('id="f-sec-05"') < security.index("<details>")
    assert security.index("<details>") < security.index('id="f-sec-01"')
    # the summary links its finding id; code lines link to GitHub at the commit
    assert f'<a href="#f-sec-05">SEC-05@{SHA[:12]}</a>' in html
    assert (
        f'href="https://github.com/example/tiny-service/blob/{SHA}/app/api/users.py#L34-L34"'
        in html
    )
    assert '<span class="ln" data-n="34"></span>' in html
    assert 'class="table-wrap"' in html


def test_html_without_a_github_repo_has_no_links() -> None:
    report = sample_report().model_copy(update={"repo_url": None})
    assert "view on GitHub" not in render_html(report, RUBRICS)
