"""Parser + severity tests per adapter, against outputs stored in tests/fixtures/scanners/."""

import json
from pathlib import Path

import pytest
import yaml

from archlens.config import ToolsConfig
from archlens.facts.base import ScanContext
from archlens.facts.scanners.actionlint import ActionlintAdapter, is_workflow
from archlens.facts.scanners.checkov import CheckovAdapter, is_iac
from archlens.facts.scanners.gitleaks import GitleaksAdapter, corrected_column, spans_from_facts
from archlens.facts.scanners.hadolint import HadolintAdapter
from archlens.facts.scanners.lizard_ import LizardAdapter
from archlens.facts.scanners.osv import OsvAdapter, cvss_to_severity, group_severity, is_manifest
from archlens.facts.scanners.semgrep import SemgrepAdapter, map_severity
from archlens.ingest.snapshot import build_snapshot
from archlens.models import CodeEvidence, Fact, IngestLimits
from tests.fixture_repos import MaterializedRepo

REPO = Path(__file__).parents[3]
FIXTURES = REPO / "tests" / "fixtures" / "scanners"
TOOLS = ToolsConfig.model_validate(yaml.safe_load((REPO / "config" / "tools.yaml").read_text()))


def ctx_for(root: Path, tmp_path: Path) -> ScanContext:
    snapshot = build_snapshot(root, limits=IngestLimits())
    return ScanContext.create(root, snapshot, tmp_path / "work", TOOLS, tmp_path / "tools")


def stored_output(tool: str, root: Path) -> str:
    return (FIXTURES / tool / "output.json").read_text().replace("__ROOT__", str(root))


def located(facts: list[Fact]) -> set[tuple[str, int]]:
    return {
        (e.path, e.start_line) for f in facts for e in f.evidence if isinstance(e, CodeEvidence)
    }


def test_gitleaks(tiny_service: MaterializedRepo, tmp_path: Path) -> None:
    ctx = ctx_for(tiny_service.root, tmp_path)
    facts = GitleaksAdapter().parse(stored_output("gitleaks", tiny_service.root), ctx, "8.30.1")
    assert {f.kind for f in facts} == {"secret"}
    assert ("app/settings_local.py", 1) in located(facts)
    first = next(f for f in facts if f.attributes["start_line"] == 1)
    line = (tiny_service.root / "app/settings_local.py").read_text().splitlines()[0]
    start, end = int(str(first.attributes["start_col"])), int(str(first.attributes["end_col"]))
    assert (
        line[start - 1 : end] == tiny_service.secret_values[0]
    )  # columns point exactly at the key
    dumped = json.dumps([f.model_dump() for f in facts])
    assert not any(v in dumped for v in tiny_service.secret_values)
    assert len(spans_from_facts(facts)) == len(facts)


def test_gitleaks_column_quirk() -> None:
    assert corrected_column(1, 22) == 22
    assert corrected_column(3, 11) == 10


def test_osv(tiny_service: MaterializedRepo, tmp_path: Path) -> None:
    ctx = ctx_for(tiny_service.root, tmp_path)
    facts = OsvAdapter().parse(stored_output("osv-scanner", tiny_service.root), ctx, "2.6.0")
    assert facts and {str(f.attributes["package"]) for f in facts} == {"pyyaml"}
    assert {f.severity for f in facts} == {"critical"}
    evidence = facts[0].evidence[0]
    assert isinstance(evidence, CodeEvidence)
    assert (evidence.path, evidence.start_line, evidence.snippet) == (
        "requirements.txt",
        1,
        "PyYAML==5.3",
    )


@pytest.mark.parametrize(
    ("score", "severity"),
    [(9.8, "critical"), (7.0, "high"), (5.3, "medium"), (0.1, "low"), (0, None)],
)
def test_cvss_to_severity(score: float, severity: str | None) -> None:
    assert cvss_to_severity(score) == severity


def test_group_severity_falls_back_to_advisory_then_default() -> None:
    vulns = [{"id": "GHSA-x", "database_specific": {"severity": "MODERATE"}}]
    assert group_severity({"ids": ["GHSA-x"], "max_severity": ""}, vulns) == ("medium", "advisory")
    assert group_severity({"ids": ["GHSA-y"], "max_severity": ""}, vulns) == ("medium", "default")
    assert group_severity({"ids": ["GHSA-x"], "max_severity": "7.5"}, vulns) == ("high", "cvss")


def test_semgrep(tiny_service: MaterializedRepo, tmp_path: Path) -> None:
    ctx = ctx_for(tiny_service.root, tmp_path)
    facts = SemgrepAdapter().parse(stored_output("semgrep", tiny_service.root), ctx, "1.179.0")
    by_rule = {str(f.attributes["rule_id"]).rsplit(".", 1)[-1]: f for f in facts}
    assert by_rule["ssrf-requests"].severity == "high"  # D04
    assert by_rule["avoid-sqlalchemy-text"].severity == "low"  # impact LOW wins over ERROR
    assert ("app/api/users.py", 59) in located([by_rule["ssrf-requests"]])
    assert ("app/main.py", 11) in located([by_rule["wildcard-cors"]])


@pytest.mark.parametrize(
    ("extra", "severity"),
    [
        ({"severity": "ERROR", "metadata": {"impact": "LOW"}}, "low"),
        ({"severity": "ERROR", "metadata": {}}, "high"),
        ({"severity": "WARNING"}, "medium"),
        ({"severity": "INFO"}, "low"),
        ({"severity": "INVENTORY"}, None),
    ],
)
def test_semgrep_severity(extra: dict[str, object], severity: str | None) -> None:
    assert map_severity(extra) == severity


def test_hadolint(tiny_service: MaterializedRepo, tmp_path: Path) -> None:
    ctx = ctx_for(tiny_service.root, tmp_path)
    facts = HadolintAdapter().parse(stored_output("hadolint", tiny_service.root), ctx, "2.15.1")
    by_code = {str(f.attributes["code"]): f for f in facts}
    assert by_code["DL4000"].severity == "high"  # D30: error level
    assert by_code["DL3007"].severity == "medium"
    assert ("Dockerfile", 2) in located([by_code["DL4000"]])


def test_checkov(tmp_path: Path) -> None:
    root = FIXTURES / "checkov" / "input"
    facts = CheckovAdapter().parse(
        stored_output("checkov", root), ctx_for(root, tmp_path), "3.3.20"
    )
    assert len(facts) > 10
    assert {str(f.attributes["framework"]) for f in facts} == {"kubernetes"}
    assert {f.severity for f in facts} == {None}  # OSS checkov reports no severity
    evidence = facts[0].evidence[0]
    assert isinstance(evidence, CodeEvidence) and evidence.path == "k8s/deployment.yaml"


def test_actionlint(tmp_path: Path) -> None:
    root = FIXTURES / "actionlint" / "input"
    facts = ActionlintAdapter().parse(
        stored_output("actionlint", root), ctx_for(root, tmp_path), "1.7.12"
    )
    assert {str(f.attributes["kind"]) for f in facts} == {"job-needs", "action", "expression"}
    assert {p for p, _ in located(facts)} == {".github/workflows/broken.yml"}


def test_lizard_runs_in_process(tiny_service: MaterializedRepo, tmp_path: Path) -> None:
    facts, record = LizardAdapter().run(ctx_for(tiny_service.root, tmp_path))
    assert record.status == "ok" and record.fact_count == len(facts)
    names = {str(f.attributes["name"]) for f in facts}
    assert {"create_user", "list_users", "search_users"} <= names
    list_users = next(f for f in facts if f.attributes["name"] == "list_users")
    assert int(str(list_users.attributes["ccn"])) >= 2  # has a loop


def test_lizard_skips_without_source(tmp_path: Path) -> None:
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "README.md").write_text("# hi\n")
    _, record = LizardAdapter().run(ctx_for(tmp_path / "repo", tmp_path))
    assert record.status == "skipped"


def test_fact_ids_are_deterministic(tiny_service: MaterializedRepo, tmp_path: Path) -> None:
    ctx = ctx_for(tiny_service.root, tmp_path)
    text = stored_output("hadolint", tiny_service.root)
    first = [f.id for f in HadolintAdapter().parse(text, ctx, "2.15.1")]
    assert first == [f.id for f in HadolintAdapter().parse(text, ctx, "2.15.1")]
    assert len(set(first)) == len(first) and all(i.startswith("hadolint_finding:") for i in first)


@pytest.mark.parametrize(
    ("predicate", "path", "expected"),
    [
        (is_manifest, "requirements.txt", True),
        (is_manifest, "requirements-dev.txt", True),
        (is_manifest, "web/package-lock.json", True),
        (is_manifest, "pyproject.toml", False),
        (is_iac, "infra/main.bicep", True),
        (is_iac, "modules/vpc/main.tf", True),
        (is_iac, "k8s/deployment.yaml", True),
        (is_iac, "docker-compose.yml", False),
        (is_iac, ".github/workflows/ci.yml", False),
        (is_workflow, ".github/workflows/ci.yml", True),
        (is_workflow, ".github/dependabot.yml", False),
    ],
)
def test_target_selection(predicate: object, path: str, expected: bool) -> None:
    assert predicate(path) is expected  # type: ignore[operator]
