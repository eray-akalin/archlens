"""Keeps tiny_service/DEFECTS.md honest: every row points at a real line and a real check."""

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.fixture_repos import ANSWER_KEY, TINY_SERVICE, MaterializedRepo

RUBRICS_DOC = Path(__file__).parents[2] / "docs" / "RUBRICS.md"
METRIC_PREFIXES = {
    "STR": "structure",
    "AUTH": "auth",
    "SEC": "security",
    "DATA": "data",
    "LOG": "logging",
    "TEST": "testing",
    "CI": "cicd",
    "CTR": "container",
    "PERF": "performance",
    "DOC": "documentation",
}
VERDICTS = {"pass", "partial", "fail", "not_applicable", "unknown"}


@dataclass(frozen=True)
class Row:
    id: str
    check: str
    expected: str
    location: str
    marker: str


def _rows() -> list[Row]:
    rows: list[Row] = []
    for line in (TINY_SERVICE / ANSWER_KEY).read_text().splitlines():
        if re.match(r"^\| [DC]\d{2} \|", line):
            cells = [c.strip() for c in line.strip("|").split("|")]
            rows.append(Row(*cells[:5]))
    return rows


ROWS = _rows()
DEFECTS = [r for r in ROWS if r.id.startswith("D")]


def test_answer_key_has_rows_and_unique_ids() -> None:
    assert len(DEFECTS) >= 10
    assert len({r.id for r in ROWS}) == len(ROWS)


def test_every_metric_has_a_defect() -> None:
    covered = {METRIC_PREFIXES[r.check.split("-")[0]] for r in DEFECTS}
    assert covered == set(METRIC_PREFIXES.values())


def test_checks_exist_in_the_catalogue() -> None:
    catalogue = set(re.findall(r"^\| ([A-Z]+-\d{2}) \|", RUBRICS_DOC.read_text(), re.MULTILINE))
    unknown = sorted({r.check for r in ROWS} - catalogue)
    assert unknown == []
    assert all(r.expected in VERDICTS for r in ROWS)


@pytest.mark.parametrize("row", ROWS, ids=[r.id for r in ROWS])
def test_location_points_at_marker(row: Row, tiny_service: MaterializedRepo) -> None:
    if row.location == "absent":
        assert row.marker == ""
        return
    injected = row.location.startswith("injected:")
    path, line = row.location.removeprefix("injected:").rsplit(":", 1)
    root = tiny_service.root if injected else TINY_SERVICE
    lines = (root / path).read_text().splitlines()
    assert row.marker in lines[int(line) - 1], f"{row.id}: {path}:{line} = {lines[int(line) - 1]!r}"
    assert (TINY_SERVICE / path).exists() is not injected, "injected files must not be committed"


def test_materialized_copy_hides_answer_key_and_injects_fresh_secrets(tmp_path: Path) -> None:
    from tests.fixture_repos import materialize_tiny_service

    first = materialize_tiny_service(tmp_path / "a")
    second = materialize_tiny_service(tmp_path / "b")
    assert not (first.root / ANSWER_KEY).exists()
    assert first.secret_values != second.secret_values
    assert re.fullmatch(r"AKIA[A-Z2-7]{16}", first.secret_values[0])


def test_committed_fixture_contains_no_secret() -> None:
    blobs = (p.read_bytes() for p in TINY_SERVICE.rglob("*") if p.is_file())
    assert not any(re.search(rb"AKIA[A-Z2-7]{16}", b) for b in blobs)


def test_fixture_has_no_lockfile_or_environment() -> None:
    """D37 depends on the lockfile being absent; a stray `uv run` inside the fixture creates one."""
    stray = [
        n
        for n in ("uv.lock", "poetry.lock", ".venv", "requirements.txt")
        if (TINY_SERVICE / n).exists()
    ]
    assert stray == []
