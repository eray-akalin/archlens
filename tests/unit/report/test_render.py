"""Markdown and HTML rendering: snapshots and escaping of repository-derived text."""

import pytest
from syrupy.assertion import SnapshotAssertion
from syrupy.extensions.single_file import SingleFileSnapshotExtension, WriteMode

from archlens.report import render_html, render_markdown
from archlens.report.render import fence, md_text
from tests.unit.report.helpers import HOSTILE_SNIPPET, NARRATIVE, RUBRICS, sample_report


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
