"""checkov → `iac_finding` facts.

Command (checkov 3.3.20)::

    checkov -d <repo> --framework kubernetes terraform cloudformation bicep arm --skip-download
        --skip-results-upload --soft-fail -o json --quiet --compact

Parses IaC files; never applies, plans or renders them. Frameworks that shell out to other
binaries (helm, kustomize) or need a Prisma API key (sca_*) are not enabled; Dockerfiles and
workflows are covered by hadolint and actionlint. External Terraform modules are never downloaded
(default; `--skip-download`). It runs from a scratch cwd with a throwaway HOME, so no repo
`.checkov.yaml` applies. Limitation: inline `checkov:skip` comments are honored and can't be
disabled.

Severity (DATA_MODEL §4): reported severity when present; OSS checkov usually reports null →
`None` (rules treat unknown severity explicitly).
"""

import json
from pathlib import PurePosixPath
from typing import Any, cast

from pydantic import JsonValue

from archlens.facts.base import ScanContext, SubprocessAdapter, evidence_or_scan, make_fact
from archlens.models import Fact, FileEntry, Severity

FRAMEWORKS = ("kubernetes", "terraform", "cloudformation", "bicep", "arm")
_IAC_DIRS = frozenset(
    {"k8s", "kubernetes", "manifests", "deploy", "deployment", "deployments", "helm", "charts",
     "infra", "infrastructure", "cloudformation", "cfn", "iac", "arm"}
)  # fmt: skip
_SEVERITY: dict[str, Severity] = {
    "CRITICAL": "critical",
    "HIGH": "high",
    "MEDIUM": "medium",
    "LOW": "low",
    "INFO": "info",
}


def is_iac(path: str) -> bool:
    """Decided from the path alone: Terraform/Bicep/ARM files, or YAML/JSON in an IaC directory."""
    p = PurePosixPath(path)
    if (
        p.suffix in {".tf", ".bicep"}
        or p.name.endswith(".tf.json")
        or p.name.startswith("azuredeploy")
    ):
        return True
    return p.suffix in {".yml", ".yaml", ".json"} and any(
        part.lower() in _IAC_DIRS for part in p.parts[:-1]
    )


class CheckovAdapter(SubprocessAdapter):
    tool = "checkov"
    binary = "checkov"

    def targets(self, files: list[FileEntry]) -> list[str]:
        return [f.path for f in files if f.readable and not f.is_vendored and is_iac(f.path)]

    def target_args(self, ctx: ScanContext, targets: list[str]) -> list[str]:
        return []  # the directory is passed in options (-d)

    def options(self, ctx: ScanContext) -> list[str]:
        return [
            "-d", str(ctx.root), "--framework", *FRAMEWORKS, "--skip-download",
            "--skip-results-upload", "--soft-fail", "-o", "json", "--quiet", "--compact",
        ]  # fmt: skip

    def parse(self, stdout: str, ctx: ScanContext, version: str) -> list[Fact]:
        data: Any = json.loads(stdout or "{}")
        blocks = cast(list[dict[str, Any]], data if isinstance(data, list) else [data])
        source = f"checkov@{version}"
        facts: list[Fact] = []
        for block in blocks:
            framework = str(block.get("check_type", ""))
            for check in block.get("results", {}).get("failed_checks", []):
                path = ctx.rel(check.get("file_abs_path") or check["file_path"])
                line_range = check.get("file_line_range") or [1, 1]
                start, end = int(line_range[0]), int(line_range[-1])
                attributes: dict[str, JsonValue] = {
                    "check_id": check["check_id"],
                    "check_name": str(check.get("check_name", "")),
                    "resource": str(check.get("resource", "")),
                    "framework": framework,
                }
                severity = _SEVERITY.get(str(check.get("severity") or "").upper())
                evidence = evidence_or_scan(
                    ctx,
                    path,
                    int(start),
                    int(end),
                    tool="checkov",
                    version=version,
                    query=check["check_id"],
                )
                facts.append(make_fact("iac_finding", source, attributes, evidence, severity))
        return facts
