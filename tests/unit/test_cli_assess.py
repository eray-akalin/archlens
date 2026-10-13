"""`archlens assess` options that need no LLM: --dry-run, metric selection, a bad --base."""

import os
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from archlens.cli import app

REPO = Path(__file__).parents[2]


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A cwd with the repo's rubrics and config but no .env, and no ARCHLENS_* variables."""
    for name in ("rubrics", "config"):
        shutil.copytree(REPO / name, tmp_path / name)
    for key in list(os.environ):
        if key.startswith("ARCHLENS_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_dry_run_projects_cost(workspace: Path) -> None:
    result = CliRunner().invoke(
        app, ["assess", str(workspace), "--dry-run", "--metrics", "security"]
    )
    assert result.exit_code == 0, result.output
    assert "security (3 LLM checks)" in result.output and "testing" not in result.output
    assert "projected (upper bound): $" in result.output and "run budget $1.00" in result.output


def test_unknown_metric_is_an_error(workspace: Path) -> None:
    result = CliRunner().invoke(app, ["assess", str(workspace), "--metrics", "security,nope"])
    assert result.exit_code == 1
    assert "nope" in result.output and "available" in result.output


@pytest.mark.parametrize(
    ("content", "expected"),
    [(None, "cannot read it"), ('{"run_id": "x"}', "not an assessment.json of schema")],
)
def test_bad_base_report_fails_before_the_run(
    workspace: Path, content: str | None, expected: str
) -> None:
    base = workspace / "base.json"
    if content is not None:
        base.write_text(content)
    result = CliRunner().invoke(app, ["assess", str(workspace), "--base", str(base)])
    assert result.exit_code == 1 and "--base" in result.output and expected in result.output
    assert "run " not in result.output  # nothing started
