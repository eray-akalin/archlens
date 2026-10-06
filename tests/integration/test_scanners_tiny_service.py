"""Real scanners on tiny_service find the planted defects (`pytest -m scanners`).

Needs the binaries from scripts/install_tools.sh. osv-scanner queries the OSV API with package
names and versions; nothing else leaves the machine and no LLM is called.
"""

import json
from pathlib import Path

import pytest

from archlens.config import Settings, load_config
from archlens.facts.base import ScanContext
from archlens.facts.scanners import run_scanners
from archlens.ingest.snapshot import build_snapshot
from archlens.models import CodeEvidence, Fact, IngestLimits
from tests.fixture_repos import MaterializedRepo, answer_key
from tests.unit.rubric.helpers import deterministic_results

pytestmark = pytest.mark.scanners
REPO = Path(__file__).parents[2]


def at(facts: list[Fact], kind: str, path: str, line: int) -> list[Fact]:
    return [
        f
        for f in facts
        if f.kind == kind
        and any(
            isinstance(e, CodeEvidence) and e.path == path and e.start_line == line
            for e in f.evidence
        )
    ]


def test_planted_defects_are_found(tiny_service: MaterializedRepo, tmp_path: Path) -> None:
    settings = Settings(_env_file=None, config_dir=REPO / "config")  # pyright: ignore[reportCallIssue]
    config = load_config(settings)
    root = tiny_service.root
    ctx = ScanContext.create(
        root,
        build_snapshot(root, limits=IngestLimits()),
        tmp_path / "work",
        config.tools,
        settings.tools_dir,
    )
    results = run_scanners(ctx)
    status = {r.tool: r.status for r in results.tool_runs}
    assert status == {
        "gitleaks": "ok",
        "osv-scanner": "ok",
        "semgrep": "ok",
        "hadolint": "ok",
        "checkov": "skipped",  # C05: no IaC
        "actionlint": "ok",
        "lizard": "ok",
    }, [r.error for r in results.tool_runs if r.error]
    facts = results.facts
    assert at(facts, "secret", "app/settings_local.py", 1)  # D01
    vulns = [
        f for f in facts if f.kind == "vuln_dependency" and f.attributes["package"] == "pyyaml"
    ]
    assert vulns and "critical" in {f.severity for f in vulns}  # D02
    assert any(
        f.severity == "high" for f in at(facts, "sast_finding", "app/api/users.py", 59)
    )  # D04
    assert at(facts, "sast_finding", "app/api/users.py", 34)  # the D03 query, low impact
    assert any(f.severity == "high" for f in at(facts, "hadolint_finding", "Dockerfile", 2))  # D30
    assert any(f.kind == "function_metrics" and f.attributes["name"] == "list_users" for f in facts)
    dumped = json.dumps([f.model_dump() for f in facts])
    assert not any(value in dumped for value in tiny_service.secret_values)


# Every fact kind a check in tiny_service/DEFECTS.md depends on (M1.5 acceptance criterion).
DEFECTS_FACT_KINDS = {
    "secret", "vuln_dependency", "sast_finding", "hadolint_finding", "function_metrics",
    "import_edge", "manifest", "dependency", "ci_workflow", "ci_step", "dockerfile",
    "deploy_config", "route", "test_file", "file_metrics", "print_call", "doc_file",
}  # fmt: skip


def test_facts_cli_writes_every_kind_defects_rely_on(
    tiny_service: MaterializedRepo, tmp_path: Path
) -> None:
    from typer.testing import CliRunner

    from archlens.cli import app

    result = CliRunner().invoke(
        app, ["facts", str(tiny_service.root), "--out", str(tmp_path / "out")]
    )
    assert result.exit_code == 0, result.output
    lines = next((tmp_path / "out").iterdir()).joinpath("facts.jsonl").read_text().splitlines()
    kinds = {Fact.model_validate_json(line).kind for line in lines}
    assert kinds >= DEFECTS_FACT_KINDS, DEFECTS_FACT_KINDS - kinds
    assert not any(value in "\n".join(lines) for value in tiny_service.secret_values)


def test_scanner_backed_checks_match_the_answer_key(
    tiny_service: MaterializedRepo, tmp_path: Path
) -> None:
    settings = Settings(_env_file=None, config_dir=REPO / "config")  # pyright: ignore[reportCallIssue]
    results = deterministic_results(
        tiny_service.root,
        tmp_path,
        scanners=True,
        tools=load_config(settings).tools,
        tools_dir=settings.tools_dir,
    )
    expected = {row.check: row.expected for row in answer_key() if row.check in results}
    for check_id in ("SEC-01", "SEC-02", "SEC-03"):
        assert results[check_id].verdict == expected[check_id], results[check_id].claim
    assert results["CI-03"].verdict == "pass", results["CI-03"].claim
    dumped = "".join(r.model_dump_json() for r in results.values())
    assert not any(value in dumped for value in tiny_service.secret_values)
