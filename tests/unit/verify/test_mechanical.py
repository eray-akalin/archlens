"""Mechanical citation check: every rejection reason, path normalization, hashes."""

from pathlib import Path

import pytest

from archlens.evidence import SnippetReader
from archlens.ingest.snapshot import build_snapshot
from archlens.models import Citation, CodeEvidence, IngestLimits
from archlens.tools.seen import SeenLines
from archlens.verify.mechanical import MechanicalResult, check_citations, normalize_path

FILE = "app/a.py"
LINES = 80


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    (root / FILE).write_text("".join(f"line_{i} = {i}\n" for i in range(1, LINES + 1)))
    (root / "app/data.bin").write_bytes(b"\x00\x01" * 10)
    (root / "outside.py").write_text("x = 1\n")
    (root / "link.py").symlink_to(root / FILE)
    return root


def check(
    repo: Path,
    *cites: tuple[str, int, int],
    seen: tuple[int, int] = (1, LINES),
    session: str | None = "s",
) -> MechanicalResult:
    listing = {f.path: f for f in build_snapshot(repo, limits=IngestLimits()).files}
    ledger = SeenLines()
    ledger.add("s", FILE, *seen)
    citations = [Citation(path=p, start_line=a, end_line=b) for p, a, b in cites]
    return check_citations(
        citations, session_id=session, reader=SnippetReader(repo), ledger=ledger, listing=listing
    )


def test_valid_citation_becomes_hashed_evidence(repo: Path) -> None:
    result = check(repo, (FILE, 3, 4))
    assert result.passed and result.step.step == "mechanical"
    (evidence,) = result.evidence
    assert (evidence.path, evidence.start_line, evidence.end_line) == (FILE, 3, 4)
    assert evidence.snippet == "line_3 = 3\nline_4 = 4"
    assert evidence == SnippetReader(repo).evidence(FILE, 3, 4)
    assert check(repo, (FILE, 1, 60)).passed  # 60 lines: end - start < 60


@pytest.mark.parametrize(
    ("cite", "message"),
    [
        ((FILE, 81, 81), "lines outside the file (1-80)"),  # invented line
        ((FILE, 0, 2), "lines outside the file"),
        ((FILE, 5, 3), "lines outside the file"),
        ((FILE, 1, 61), "span of 61 lines"),
        ((FILE, 30, 31), "never shown to the session (30, 31)"),  # unseen
        (("app/missing.py", 1, 1), "not a readable file"),
        (("../etc/passwd", 1, 1), "outside the snapshot"),
        (("/etc/passwd", 1, 1), "outside the snapshot"),
        (("app/data.bin", 1, 1), "not a readable file"),
        (("link.py", 1, 1), "not a readable file"),  # symlinks are listed, never read
    ],
)
def test_rejections(repo: Path, cite: tuple[str, int, int], message: str) -> None:
    result = check(repo, cite, seen=(1, 29))
    assert not result.passed and result.evidence == []
    assert message in result.step.detail


def test_one_bad_citation_fails_the_whole_step(repo: Path) -> None:
    result = check(repo, (FILE, 2, 3), (FILE, 70, 71), seen=(1, 10))
    assert not result.passed and "70-71" in result.step.detail


def test_lines_seen_by_another_session_do_not_count(repo: Path) -> None:
    assert not check(repo, (FILE, 2, 3), session="other").passed
    assert "no session" in check(repo, (FILE, 2, 3), session=None).step.detail


def test_no_citations_fail(repo: Path) -> None:
    assert check(repo).step.detail == "no citations"


def test_paths_are_normalized(repo: Path) -> None:
    result = check(repo, ("./app/../app/a.py", 2, 2))
    assert result.passed and result.evidence[0].path == FILE
    assert normalize_path(" .\\app\\a.py ") == FILE and normalize_path(".github/x") == ".github/x"


def test_existing_evidence_must_match_the_snapshot(repo: Path) -> None:
    listing = {f.path: f for f in build_snapshot(repo, limits=IngestLimits()).files}
    ledger = SeenLines()
    ledger.add("s", FILE, 1, LINES)
    reader = SnippetReader(repo)
    good = reader.evidence(FILE, 2, 3)
    assert good is not None
    forged = good.model_copy(update={"snippet": "tampered", "snippet_sha256": "0" * 64})
    citations = [Citation(path=FILE, start_line=2, end_line=3)]

    def run(existing: list[CodeEvidence]) -> MechanicalResult:
        return check_citations(
            citations, session_id="s", reader=reader, ledger=ledger, listing=listing,
            existing=existing,
        )  # fmt: skip

    assert run([good]).passed
    failed = run([forged])
    assert not failed.passed and "snippet hash mismatch" in failed.step.detail
