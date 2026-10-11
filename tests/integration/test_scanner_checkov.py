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
from archlens.models import CodeEvidence, IngestLimits

pytestmark = pytest.mark.scanners
REPO = Path(__file__).parents[2]
INPUT = REPO / "tests" / "fixtures" / "scanners" / "checkov" / "input"


def test_checkov_ignores_repo_config(tmp_path: Path) -> None:
    """A repo `.checkov.yaml` is not loaded: here it would skip CKV_K8S_8; with
    `external-checks-dir`/`-git` it would make checkov import Python from the repo."""
    root = tmp_path / "repo"
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
    assert "CKV_K8S_8" in {str(f.attributes["check_id"]) for f in findings}
    assert {e.path for f in findings for e in f.evidence if isinstance(e, CodeEvidence)} == {
        "k8s/deployment.yaml",
        "infra/storage.bicep",
    }
    (unparsed,) = [f for f in facts if f.kind == "iac_parse_error"]
    assert unparsed.attributes["path"] == "infra/typed.bicep"
