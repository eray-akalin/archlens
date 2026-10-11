"""Real checkov on the stored fixture input (`pytest -m scanners`): how the adapter hands it the
directory. Needs checkov from scripts/install_tools.sh; nothing leaves the machine.
"""

import shutil
from pathlib import Path

import pytest

from archlens.config import Settings, load_config
from archlens.facts.base import ScanContext
from archlens.facts.scanners.checkov import CheckovAdapter
from archlens.ingest.snapshot import build_snapshot
from archlens.models import CodeEvidence, FactSet, IngestLimits, RepoProfile
from archlens.rubric import RuleContext, load_rubrics, run_rule

pytestmark = pytest.mark.scanners
REPO = Path(__file__).parents[2]
INPUT = REPO / "tests" / "fixtures" / "scanners" / "checkov" / "input"


@pytest.mark.parametrize("parent", ["plain", ".hidden"])
def test_checkov_scans_the_repo_without_its_config(parent: str, tmp_path: Path) -> None:
    """Under a hidden directory (scanned through a symlink) the kubernetes findings are still
    there and map back to repo paths. A repo `.checkov.yaml` is never loaded: here it would skip
    CKV_K8S_8; with `external-checks-dir`/`-git` it would make checkov import Python."""
    root = tmp_path / parent / "input"
    shutil.copytree(INPUT, root)
    (root / ".checkov.yaml").write_text("skip-check:\n  - CKV_K8S_8\n")
    settings = Settings(_env_file=None, config_dir=REPO / "config")  # pyright: ignore[reportCallIssue]
    ctx = ScanContext.create(
        root,
        build_snapshot(root, limits=IngestLimits()),
        tmp_path / "work",
        load_config(settings).tools,
        settings.tools_dir,
    )
    facts, record = CheckovAdapter().run(ctx)
    assert record.status == "ok", record.error
    findings = [f for f in facts if f.kind == "iac_finding"]
    assert {str(f.attributes["framework"]) for f in findings} == {"kubernetes", "bicep"}
    assert "CKV_K8S_8" in {str(f.attributes["check_id"]) for f in findings}
    assert {e.path for f in findings for e in f.evidence if isinstance(e, CodeEvidence)} == {
        "k8s/deployment.yaml",
        "infra/storage.bicep",
    }
    (unparsed,) = [f for f in facts if f.kind == "iac_parse_error"]
    assert unparsed.attributes["path"] == "infra/typed.bicep"


def test_iac_only_under_a_hidden_directory_is_not_a_pass(tmp_path: Path) -> None:
    """checkov skips `.infra/` without a word; CTR-06 must say unknown, not pass."""
    root = tmp_path / "repo"
    (root / ".infra" / "k8s").mkdir(parents=True)
    shutil.copy(INPUT / "k8s" / "deployment.yaml", root / ".infra" / "k8s" / "deployment.yaml")
    settings = Settings(_env_file=None, config_dir=REPO / "config")  # pyright: ignore[reportCallIssue]
    snapshot = build_snapshot(root, limits=IngestLimits())
    ctx = ScanContext.create(
        root, snapshot, tmp_path / "work", load_config(settings).tools, settings.tools_dir
    )
    facts, record = CheckovAdapter().run(ctx)
    assert record.status == "ok", record.error
    assert [f for f in facts if f.kind == "iac_finding"] == []  # checkov really skipped it
    (unchecked,) = [f for f in facts if f.kind == "iac_parse_error"]
    assert unchecked.attributes["reason"] == "hidden_directory"
    rubric = load_rubrics(REPO / "rubrics")["container"]
    check = next(c for c in rubric.checks if c.id == "CTR-06")
    fact_set = FactSet(commit_sha="0" * 40, facts=facts, tool_runs=[record])
    profile = RepoProfile(
        languages={}, frameworks=[], package_managers=[], ci_systems=[], test_frameworks=[],
        flags={"has_iac": True}, entrypoints=[],
    )  # fmt: skip
    result = run_rule(check, rubric, RuleContext.create(root, fact_set, profile, snapshot.files))
    assert (result.verdict, result.reason) == ("unknown", "iac_unparsed")
