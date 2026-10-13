"""`pr_comment.md`: score deltas and finding changes against a base report."""

import re

from archlens.models import AssessmentReport, ScanEvidence
from archlens.report import render_pr_comment
from archlens.report.pr import MARKER, comment_text
from tests.unit.report.helpers import RUBRICS, SHA, code, finding, sample_report

HEAD_SHA = "fedcba9876543210fedcba9876543210fedcba98"


def head_report(claim: str = "") -> AssessmentReport:
    """SEC-05 fixed, SEC-07 fail → partial, TEST-01 pass → fail."""
    missing = ScanEvidence(tool="files", tool_version="0.1.0", query="q", result_count=0)
    return sample_report(commit_sha=HEAD_SHA, found=[
        finding("security", "SEC-05", "pass", evidence=[code("app/api/users.py", 34, "q(?)")]),
        finding("security", "SEC-07", "partial", evidence=[missing]),
        finding("security", "SEC-01", "pass", evidence=[missing]),
        finding("testing", "TEST-01", "fail", claim=claim or "the suite is empty",
                evidence=[code("tests/test_users.py", 1, "pass")]),
    ])  # fmt: skip


def scores(report: AssessmentReport) -> dict[str, float | None]:
    return {m.metric: m.score for m in report.metric_scores}


def test_deltas_and_changes() -> None:
    base, head = sample_report(), head_report()
    text = render_pr_comment(base, head, RUBRICS, reused=["testing"])
    lines = text.splitlines()
    assert lines[0] == MARKER
    before, after = scores(base)["security"], scores(head)["security"]
    assert before is not None and after is not None
    assert f"| Security | {before:.1f} | {after:.1f} | {after - before:+.1f} |" in text
    assert f"Base `{SHA[:12]}` → head `{HEAD_SHA[:12]}`" in text
    # TEST-01 is llm-origin in the helpers; SEC-* too: both metrics have LLM checks
    assert (
        "LLM re-evaluated 1 of 2 metrics with LLM checks (unchanged inputs, reused: testing)"
        in text
    )
    assert (
        "### New issues (1)\n\n- **TEST-01** A test suite is present (high): pass → **fail**"
        in text
    )
    assert "`tests/test_users.py:1-1`<br>the suite is empty" in text
    assert "### Resolved (1)\n\n- **SEC-05** Database queries are parameterized (critical): " \
           "fail → **pass**" in text  # fmt: skip
    assert (
        "### Changed (1)\n\n- **SEC-07** Automated dependency updates (low): fail → **partial**"
        in text
    )


def test_overall_and_unscored_metrics() -> None:
    base = sample_report()
    head = sample_report(found=[], commit_sha=HEAD_SHA)  # nothing verified: nothing scored
    text = render_pr_comment(base, head, RUBRICS)
    assert "→ not computed" in text.splitlines()[1]
    assert "| Security | " in text and "insufficient evidence" in text
    assert "LLM re-evaluated 0 of 0 metrics with LLM checks · " in text  # nothing judged


def test_no_changes() -> None:
    text = render_pr_comment(sample_report(), sample_report(commit_sha=HEAD_SHA), RUBRICS)
    assert "No verified finding changed." in text and "±0.0" in text


def test_other_rubric_versions_are_flagged() -> None:
    newer = {k: r.model_copy(update={"version": "2.0.0"}) for k, r in RUBRICS.items()}
    head = sample_report(commit_sha=HEAD_SHA, rubrics=newer)
    assert "other rubric versions" in render_pr_comment(sample_report(), head, RUBRICS)


def test_claims_cannot_mention_link_or_embed() -> None:
    hostile = "@octocat see [docs](https://evil.example) ![x](https://t.example/p.png) <b>|</b>"
    text = render_pr_comment(sample_report(), head_report(hostile), RUBRICS)
    assert "@octocat" not in text and "@​octocat" in text
    assert not re.search(r"(?<!\\)\]\(", text) and "<b>" not in text  # no live link syntax
    assert comment_text("a [b](c) !d") == "a \\[b\\](c) \\!d"
