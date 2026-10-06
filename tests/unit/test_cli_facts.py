import json
from pathlib import Path

from typer.testing import CliRunner

from archlens.cli import app
from archlens.models import Fact, ToolRunRecord
from tests.fixture_repos import MaterializedRepo

REQUIRED_EXTRACTOR_KINDS = {
    "import_edge", "manifest", "dependency", "ci_workflow", "ci_step", "dockerfile",
    "deploy_config", "route", "test_file", "file_metrics", "print_call", "doc_file",
}  # fmt: skip


def test_facts_command_extractors_only(tiny_service: MaterializedRepo, tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app, ["facts", str(tiny_service.root), "--out", str(tmp_path / "out"), "--no-scanners"]
    )
    assert result.exit_code == 0, result.output
    run_dir = next((tmp_path / "out").iterdir())
    facts = [
        Fact.model_validate_json(line)
        for line in (run_dir / "facts.jsonl").read_text().splitlines()
    ]
    assert {f.kind for f in facts} >= REQUIRED_EXTRACTOR_KINDS
    runs = [
        ToolRunRecord.model_validate(r)
        for r in json.loads((run_dir / "tool_runs.json").read_text())
    ]
    assert {r.status for r in runs} == {"ok"}
    assert "by kind:" in result.output


def test_facts_command_reports_ingest_errors(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["facts", str(tmp_path / "missing"), "--no-scanners"])
    assert result.exit_code == 1 and "ingest failed" in result.output
