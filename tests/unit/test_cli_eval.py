"""`archlens eval` planning commands that need no LLM (no .env, no ARCHLENS_* variables)."""

import os
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from archlens.cli import app

REPO = Path(__file__).parents[2]


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in ("rubrics", "config", "prompts"):
        shutil.copytree(REPO / name, tmp_path / name)
    (tmp_path / "eval" / "configs").mkdir(parents=True)
    shutil.copy(REPO / "eval" / "repos.yaml", tmp_path / "eval" / "repos.yaml")
    shutil.copy(
        REPO / "eval" / "configs" / "full.yaml", tmp_path / "eval" / "configs" / "full.yaml"
    )
    for key in list(os.environ):
        if key.startswith("ARCHLENS_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_dry_run_with_zero_mutations(workspace: Path) -> None:
    result = CliRunner().invoke(app, ["eval", "--config", "eval/configs/full.yaml", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "primary.base.2" in result.output and "cache off" in result.output
    assert "4 runs, projected $" in result.output and "budget $5.00" in result.output


def test_plan_problems_stop_the_run(workspace: Path) -> None:
    config = yaml.safe_load((workspace / "eval/configs/full.yaml").read_text())
    config["suite"].append({"repo": "primary", "variant": "V1"})
    (workspace / "eval/configs/bad.yaml").write_text(yaml.safe_dump(config))
    (workspace / "eval/variants.yaml").write_text(
        yaml.safe_dump({"variants": [{"id": "V1", "repo": "primary", "mutations": ["M-ROOT"]}]})
    )
    result = CliRunner().invoke(app, ["eval", "--config", "eval/configs/bad.yaml", "--dry-run"])
    assert result.exit_code == 1
    assert "unknown mutation M-ROOT" in result.output


def test_eval_without_config_shows_help(workspace: Path) -> None:
    result = CliRunner().invoke(app, ["eval"])
    assert result.exit_code == 1 and "fetch" in result.output
