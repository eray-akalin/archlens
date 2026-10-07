"""Regenerate tests/fixtures/scanners/<tool>/output.json from each adapter's real command.

Usage: uv run python scripts/capture_scanner_fixtures.py   (needs the scanners; osv-scanner
queries the OSV API). Inputs: a materialized tiny_service for most tools; checkov and actionlint
use the small inputs under tests/fixtures/scanners/<tool>/input/. Output is redacted, the input
root is replaced by __ROOT__, and a README records the tool version and exact command.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tests.fixture_repos import materialize_tiny_service  # noqa: E402

from archlens.config import load_config  # noqa: E402
from archlens.facts.base import (  # noqa: E402
    ScanContext,
    SubprocessAdapter,
    installed_version,
    tool_env,
)
from archlens.facts.scanners.actionlint import ActionlintAdapter  # noqa: E402
from archlens.facts.scanners.checkov import CheckovAdapter  # noqa: E402
from archlens.facts.scanners.gitleaks import GitleaksAdapter  # noqa: E402
from archlens.facts.scanners.hadolint import HadolintAdapter  # noqa: E402
from archlens.facts.scanners.osv import OsvAdapter  # noqa: E402
from archlens.facts.scanners.semgrep import SemgrepAdapter  # noqa: E402
from archlens.ingest.snapshot import build_snapshot  # noqa: E402
from archlens.models import IngestLimits  # noqa: E402
from archlens.security.redact import redact  # noqa: E402

FIXTURES = REPO / "tests" / "fixtures" / "scanners"
ROOT_TOKEN = "__ROOT__"


def capture(adapter: SubprocessAdapter, root: Path, input_label: str) -> None:
    config = load_config()
    snapshot = build_snapshot(root, limits=IngestLimits())
    with tempfile.TemporaryDirectory() as work:
        ctx = ScanContext.create(
            root, snapshot, Path(work), config.tools, config.settings.tools_dir
        )
        targets = adapter.targets(snapshot.files)
        exe = shutil.which(adapter.binary)
        assert exe and targets, f"{adapter.tool}: binary or targets missing"
        argv = [exe, *adapter.options(ctx), *adapter.target_args(ctx, targets)]
        proc = subprocess.run(
            argv,
            cwd=adapter.cwd(ctx),
            env=tool_env(ctx.workdir),
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode in adapter.ok_exit_codes, proc.stderr
        text = proc.stdout
        for real in sorted({str(root.resolve()), str(root)}, key=len, reverse=True):
            text = text.replace(real, ROOT_TOKEN)
        data = json.loads(redact(text))
        shown = " ".join(SubprocessAdapter._placeholders(argv[1:], ctx))  # pyright: ignore[reportPrivateUsage]
    out = FIXTURES / adapter.tool
    out.mkdir(parents=True, exist_ok=True)
    (out / "output.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    version = installed_version(exe, *adapter.version_args)
    (out / "README.md").write_text(
        f"# {adapter.tool} fixture\n\n"
        f"- Tool version: {version}\n"
        f"- Captured: {datetime.now(UTC).date()} by `scripts/capture_scanner_fixtures.py`\n"
        f"- Input: {input_label}\n"
        f"- Command (`<repo>`, `<work>`, `<tools>` are placeholders):\n\n"
        f"  ```\n  {adapter.binary} {shown}\n  ```\n\n"
        "Output is redacted and the input root is replaced by `__ROOT__`.\n"
    )
    print(f"{adapter.tool}: {len(proc.stdout)} bytes captured")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tiny = materialize_tiny_service(Path(tmp) / "tiny_service").root
        label = "materialized `tests/fixtures/repos/tiny_service` (with injected D01/D02)"
        for adapter in (GitleaksAdapter(), OsvAdapter(), SemgrepAdapter(), HadolintAdapter()):
            capture(adapter, tiny, label)
    for adapter in (CheckovAdapter(), ActionlintAdapter()):
        capture(
            adapter,
            FIXTURES / adapter.tool / "input",
            f"`tests/fixtures/scanners/{adapter.tool}/input/`",
        )


if __name__ == "__main__":
    main()
