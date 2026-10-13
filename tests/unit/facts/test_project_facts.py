"""Facts from vendored code and test data are dropped — except secrets and injection attempts."""

from datetime import UTC, datetime

from archlens.facts.base import make_fact
from archlens.facts.runner import project_facts
from archlens.models import CodeEvidence, FileEntry, RepoSnapshot, ScanEvidence

FIXTURE = "tests/fixtures/repo/Dockerfile"


def entry(path: str, vendored: bool) -> FileEntry:
    return FileEntry(path=path, size=1, sha256="a" * 64, language=None, loc=1, is_binary=False,
                     is_generated=False, is_vendored=vendored)  # fmt: skip


def code(path: str) -> CodeEvidence:
    return CodeEvidence(path=path, start_line=1, end_line=1, snippet="x", snippet_sha256="b" * 64)


SNAPSHOT = RepoSnapshot(
    repo_url=None, ref=None, commit_sha="c" * 40, created_at=datetime(2026, 10, 13, tzinfo=UTC),
    files=[entry("Dockerfile", False), entry(FIXTURE, True)],
)  # fmt: skip


def test_fixture_only_evidence_is_dropped_unless_it_matters_everywhere() -> None:
    scan = ScanEvidence(tool="hadolint", tool_version="2", query="q", result_count=0)
    facts = [
        make_fact("dockerfile_issue", "t", {"at": "fixture"}, [code(FIXTURE)]),
        make_fact("dockerfile_issue", "t", {"at": "project"}, [code("Dockerfile")]),
        make_fact("dockerfile_issue", "t", {"at": "both"}, [code(FIXTURE), code("Dockerfile")]),
        make_fact("tool_scan", "t", {"at": "none"}, [scan]),
        make_fact("secret", "t", {"at": "fixture"}, [code(FIXTURE)]),
        make_fact("injection_attempt", "t", {"at": "fixture"}, [code(FIXTURE)]),
    ]
    kept = [(f.kind, f.attributes["at"]) for f in project_facts(facts, SNAPSHOT)]
    assert kept == [
        ("dockerfile_issue", "project"), ("dockerfile_issue", "both"), ("tool_scan", "none"),
        ("secret", "fixture"), ("injection_attempt", "fixture"),
    ]  # fmt: skip
