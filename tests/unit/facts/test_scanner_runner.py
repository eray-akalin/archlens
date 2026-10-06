"""Runner behavior: skipped / missing / failing / timeout / unparseable never crash; env hygiene."""

import json
import sys
from pathlib import Path
from typing import ClassVar

import pytest

from archlens.config import ToolConfig, ToolsConfig
from archlens.facts.base import ScanContext
from archlens.facts.scanners import ScanResults, run_scanners
from archlens.facts.scanners.gitleaks import GitleaksAdapter
from archlens.facts.scanners.hadolint import HadolintAdapter
from archlens.ingest.snapshot import build_snapshot
from archlens.models import Fact, IngestLimits, ToolRunRecord

TOOLS = ToolsConfig(tools={"hadolint": ToolConfig(version="9.9.9", timeout_s=1)})


def repo_with_dockerfile(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "Dockerfile").write_text("FROM python:3.12\n")
    return root


def ctx_for(root: Path, tmp_path: Path) -> ScanContext:
    snapshot = build_snapshot(root, limits=IngestLimits())
    return ScanContext.create(root, snapshot, tmp_path / "work", TOOLS, tmp_path / "tools")


def fake_hadolint(tmp_path: Path, body: str) -> type[HadolintAdapter]:
    script = tmp_path / "fake-hadolint"
    script.write_text(
        f"#!{sys.executable}\nimport json, os, sys, time\n"
        "if '--version' in sys.argv:\n    print('fake 9.9.9'); sys.exit(0)\n" + body + "\n"
    )
    script.chmod(0o755)

    class Fake(HadolintAdapter):
        binary: ClassVar[str] = str(script)

    return Fake


def test_no_targets_is_skipped(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("x = 1\n")
    facts, record = HadolintAdapter().run(ctx_for(root, tmp_path))
    assert (facts, record.status, record.fact_count) == ([], "skipped", 0)


def test_missing_binary_is_an_error(tmp_path: Path) -> None:
    class Missing(HadolintAdapter):
        binary: ClassVar[str] = "definitely-not-installed-archlens"

    facts, record = Missing().run(ctx_for(repo_with_dockerfile(tmp_path), tmp_path))
    assert facts == [] and record.status == "error" and "not found" in str(record.error)


def test_failing_binary_is_an_error(tmp_path: Path) -> None:
    adapter = fake_hadolint(
        tmp_path, "sys.stderr.write('boom token=Zx9Kq2Lm7Vb4Np8Rt3Wy\\n'); sys.exit(3)"
    )()
    _, record = adapter.run(ctx_for(repo_with_dockerfile(tmp_path), tmp_path))
    assert record.status == "error" and "exit 3" in str(record.error)
    assert "Zx9Kq2Lm7Vb4Np8Rt3Wy" not in str(record.error)  # stderr is redacted before recording


def test_unparseable_output_is_an_error(tmp_path: Path) -> None:
    adapter = fake_hadolint(tmp_path, "print('not json'); sys.exit(0)")()
    _, record = adapter.run(ctx_for(repo_with_dockerfile(tmp_path), tmp_path))
    assert record.status == "error" and "unparseable" in str(record.error)


def test_timeout(tmp_path: Path) -> None:
    adapter = fake_hadolint(tmp_path, "time.sleep(5)")()
    _, record = adapter.run(ctx_for(repo_with_dockerfile(tmp_path), tmp_path))
    assert record.status == "timeout"


def test_tools_get_a_minimal_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCHLENS_LLM_API_KEY", "sk-should-never-reach-a-scanner")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "nope")
    adapter = fake_hadolint(
        tmp_path, "json.dump(dict(os.environ), open('env.json', 'w')); print('[]')"
    )()
    ctx = ctx_for(repo_with_dockerfile(tmp_path), tmp_path)
    _, record = adapter.run(ctx)
    env = json.loads((ctx.workdir / "env.json").read_text())  # the tool ran with cwd=workdir
    assert record.status == "ok"
    assert not any(k.startswith(("ARCHLENS_", "AZURE_")) for k in env)
    assert env["HOME"] == str(ctx.workdir / "home")
    assert str(ctx.root) not in " ".join(record.command) and "<1 targets>" in record.command


def test_gitleaks_runs_first_and_feeds_the_redactor(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "cfg.py").write_text('token = "zz-opaque-value-without-pattern"\n')
    seen: list[str] = []

    class StubGitleaks(GitleaksAdapter):
        def run(self, ctx: ScanContext) -> tuple[list[Fact], ToolRunRecord]:
            seen.append("gitleaks")
            finding = {"File": "cfg.py", "RuleID": "custom", "StartLine": 1, "EndLine": 1}
            text = json.dumps([finding | {"StartColumn": 10, "EndColumn": 41}])
            return self.parse(text, ctx, "x"), ToolRunRecord(
                tool="gitleaks", version="x", command=[], status="ok", duration_ms=0, fact_count=1
            )

    class Probe:
        def run(self, ctx: ScanContext) -> tuple[list[Fact], ToolRunRecord]:
            seen.append("probe")
            evidence = ctx.reader.evidence("cfg.py", 1)
            assert evidence is not None and "zz-opaque" not in evidence.snippet
            return [], ToolRunRecord(
                tool="probe", version="x", command=[], status="ok", duration_ms=0, fact_count=0
            )

    results: ScanResults = run_scanners(ctx_for(root, tmp_path), [Probe(), StubGitleaks()])
    assert seen == ["gitleaks", "probe"]
    assert [r.tool for r in results.tool_runs] == ["gitleaks", "probe"]
    assert "zz-opaque" not in json.dumps([f.model_dump() for f in results.facts])
