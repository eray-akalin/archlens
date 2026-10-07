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
    for name in ("repos.yaml", "variants.yaml"):
        shutil.copy(REPO / "eval" / name, tmp_path / "eval" / name)
    shutil.copy(
        REPO / "eval" / "configs" / "full.yaml", tmp_path / "eval" / "configs" / "full.yaml"
    )
    for key in list(os.environ):
        if key.startswith("ARCHLENS_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_dry_run_lists_runs_and_mutations(workspace: Path) -> None:
    result = CliRunner().invoke(app, ["eval", "--config", "eval/configs/full.yaml", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "primary.base.2" in result.output and "cache off" in result.output
    assert "M-SQLI + I-COMMENT" in result.output and "cross.VX1.0" in result.output
    assert "11 runs, projected $" in result.output and "budget $5.00" in result.output


def test_plan_problems_stop_the_run(workspace: Path) -> None:
    config = yaml.safe_load((workspace / "eval/configs/full.yaml").read_text())
    config["suite"] = [{"repo": "primary", "variant": "V9"}]
    config["variants_file"] = "eval/bad-variants.yaml"
    (workspace / "eval/configs/bad.yaml").write_text(yaml.safe_dump(config))
    (workspace / "eval/bad-variants.yaml").write_text(
        yaml.safe_dump({"variants": [{"id": "V9", "repo": "primary", "mutations": ["M-NOPE"]}]})
    )
    result = CliRunner().invoke(app, ["eval", "--config", "eval/configs/bad.yaml", "--dry-run"])
    assert result.exit_code == 1
    assert "unknown mutation M-NOPE" in result.output


def test_eval_without_config_shows_help(workspace: Path) -> None:
    result = CliRunner().invoke(app, ["eval"])
    assert result.exit_code == 1 and "fetch" in result.output
