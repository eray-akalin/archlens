"""`archlens eval` planning commands that need no LLM (no .env, no ARCHLENS_* variables)."""

import os
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from archlens.cli import app
from tests.unit.eval.helpers import finding, run

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
    assert "13 runs, projected $" in result.output and "budget $5.00" in result.output


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


def test_label_sheet_write_and_read(workspace: Path) -> None:
    report = run("base", [finding("SEC-05", "fail")]).report
    (workspace / "report.json").write_text(report.model_dump_json())
    args = ["eval", "label-sheet", "primary", "--report", "report.json"]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    sheet = workspace / "eval/labels/primary.sheet.md"
    assert "Current: **fail**" in sheet.read_text()
    again = CliRunner().invoke(app, args)
    assert again.exit_code == 1 and "--force overwrites it" in again.output

    filled = sheet.read_text().replace("- label:", "- label: pass", 2)
    sheet.write_text(filled)
    read = CliRunner().invoke(app, ["eval", "label-sheet", "primary", "--read"])
    assert read.exit_code == 0, read.output
    assert "2 labels over 1 metrics" in read.output and "at least 6 metrics" in read.output
    labels = yaml.safe_load((workspace / "eval/labels/primary.yaml").read_text())
    assert labels["repo"] == "primary" and len(labels["labels"]) == 2
    assert len(labels["commit"]) == 40

    unknown = CliRunner().invoke(app, ["eval", "label-sheet", "nope"])
    assert unknown.exit_code == 1 and "unknown eval repo 'nope'" in unknown.output


def test_audit_sheet_and_report(workspace: Path) -> None:
    results = workspace / "eval/results/full-1"
    (results / "variants").mkdir(parents=True)
    shutil.copy(workspace / "eval/configs/full.yaml", results / "config.yaml")
    record = run("base", [finding("SEC-05", "fail"), finding("DOC-01", "pass")])
    (results / "variants" / f"{record.name}.json").write_text(record.model_dump_json())

    result = CliRunner().invoke(app, ["eval", "audit", "eval/results/full-1", "--sample", "5"])
    assert result.exit_code == 0, result.output
    assert "(2 findings)" in result.output
    sheet = workspace / "eval/labels/audit-full-1.md"
    sheet.write_text(sheet.read_text().replace("- agree:", "- agree: yes", 1))
    read = CliRunner().invoke(app, ["eval", "audit", "eval/results/full-1", "--read"])
    assert read.exit_code == 0 and "1 answers, 1 agree" in read.output

    report = CliRunner().invoke(app, ["eval", "report", "eval/results/full-1"])
    assert report.exit_code == 0, report.output
    assert "| Verifier precision (audit) | 100% |" in report.output
