"""semgrep → `sast_finding` facts.

Command (semgrep 1.179.0)::

    semgrep scan --config <tools>/semgrep/p-default.yml --metrics off --no-git-ignore
        --disable-nosem --json --quiet --max-target-bytes <max_file_bytes> <file> <file> ...

Static analysis; never executes repo code. Rules come from the pinned pack installed by
`scripts/install_tools.sh` (registry name as fallback); repo rule files are never loaded. Targets
are passed as explicit files because a repo `.semgrepignore` hides files from directory scans
(verified: `*` in it → 0 results on 1.179.0) but not from explicit targets. `--disable-nosem`
ignores inline `nosemgrep` suppressions.

Severity (DATA_MODEL §4): `metadata.impact` when present, else `extra.severity`.
"""

import json
from typing import Any

from pydantic import JsonValue

from archlens.facts.base import ScanContext, SubprocessAdapter, evidence_or_scan, make_fact
from archlens.models import Fact, FileEntry, Severity
from archlens.security.redact import redact

LANGUAGES = frozenset(
    {
        "python", "javascript", "typescript", "java", "kotlin", "scala", "go", "csharp",
        "ruby", "php", "rust", "c", "cpp", "swift", "shell", "terraform", "dockerfile",
        "yaml", "json", "html",
    }
)  # fmt: skip
_IMPACT: dict[str, Severity] = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}
_LEVEL: dict[str, Severity] = {"ERROR": "high", "WARNING": "medium", "INFO": "low"}


def map_severity(extra: dict[str, Any]) -> Severity | None:
    impact = str(extra.get("metadata", {}).get("impact", "")).upper()
    if impact in _IMPACT:
        return _IMPACT[impact]
    return _LEVEL.get(str(extra.get("severity", "")).upper())


def ruleset_config(ctx: ScanContext, ruleset: str) -> str:
    """Local pinned file for a registry ruleset (`p/default` → p-default.yml) if installed."""
    local = ctx.tools_dir / "semgrep" / f"{ruleset.replace('/', '-')}.yml"
    return str(local) if local.is_file() else ruleset


class SemgrepAdapter(SubprocessAdapter):
    tool = "semgrep"
    binary = "semgrep"
    ok_exit_codes = frozenset({0, 1})

    def targets(self, files: list[FileEntry]) -> list[str]:
        return [
            f.path
            for f in files
            if f.readable and not f.is_vendored and not f.is_generated and f.language in LANGUAGES
        ]

    def options(self, ctx: ScanContext) -> list[str]:
        config = ctx.tool(self.tool)
        argv = ["scan"]
        for ruleset in config.rulesets or ["p/default"]:
            argv += ["--config", ruleset_config(ctx, ruleset)]
        return [
            *argv,
            "--metrics", config.metrics or "off",
            "--no-git-ignore",
            "--disable-nosem",
            "--json",
            "--quiet",
            "--max-target-bytes", str(ctx.reader.max_file_bytes),
        ]  # fmt: skip

    def parse(self, stdout: str, ctx: ScanContext, version: str) -> list[Fact]:
        source = f"semgrep@{version}"
        facts: list[Fact] = []
        for result in json.loads(stdout or "{}").get("results", []):
            path = ctx.rel(result["path"])
            extra: dict[str, Any] = result.get("extra", {})
            attributes: dict[str, JsonValue] = {
                "rule_id": result["check_id"],
                "category": str(extra.get("metadata", {}).get("category", "")),
                "message": redact(str(extra.get("message", "")))[:500],
            }
            start, end = int(result["start"]["line"]), int(result["end"]["line"])
            evidence = evidence_or_scan(
                ctx, path, start, end, tool="semgrep", version=version, query=result["check_id"]
            )
            facts.append(
                make_fact("sast_finding", source, attributes, evidence, map_severity(extra))
            )
        return facts
