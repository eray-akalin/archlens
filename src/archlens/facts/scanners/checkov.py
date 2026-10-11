"""checkov → `iac_finding` and `iac_parse_error` facts.

Command (checkov 3.3.20)::

    checkov --directory=<repo> --framework kubernetes terraform cloudformation bicep arm
        --skip-download --skip-results-upload --soft-fail -o json --compact

`--directory=<repo>`, one argv token: checkov also loads `.checkov.yaml`/`.checkov.yml` from the
directory it scans, but only finds it when `-d`/`--directory` and the path are separate tokens.
A repo config could suppress checks (`skip-check`) or make checkov import Python from the repo or
a git URL (`external-checks-dir`, `external-checks-git`), i.e. execute repository code. The
`scanners` suite pins this down.

Hidden directories: checkov's kubernetes runner silently skips every file whose absolute path
contains `/.` (no result block, no parse error), so a root such as a `.claude/worktrees/...`
checkout would read as clean. For a root with a hidden component the adapter scans
`<work>/checkov-root`, a symlink to it; checkov follows it and `ScanContext.rel` resolves the
reported paths (`file_abs_path`, `parsing_errors`) back to the repo. If the scratch dir has a
hidden component too, the run is status error rather than a silent gap. Inside the repo checkov
skips hidden directories too (the kubernetes runner's `/.` test regardless of
`CKV_IGNORE_HIDDEN_DIRECTORIES`; the other runners by that default) — and scanning them would also
reach `.git/`. So they stay skipped, and every non-vendored IaC file under a hidden directory
(or itself hidden) becomes an `iac_parse_error` with `reason: hidden_directory`: unchecked, never
read as clean.

No `--quiet`: it drops `results.parsing_errors`, the files checkov could not parse and so never
checked (e.g. Bicep with user-defined `type` declarations). Each becomes an `iac_parse_error`
fact; a summary that counts more parse errors than it lists is unparseable output (status error),
so unchecked files are never silently read as clean.

Parses IaC files; never applies, plans or renders them. Frameworks that shell out to other
binaries (helm, kustomize) or need a Prisma API key (sca_*) are not enabled; Dockerfiles and
workflows are covered by hadolint and actionlint. External Terraform modules are never downloaded
(default; `--skip-download`). It runs from a scratch cwd with a throwaway HOME, so no other
`.checkov.yaml` applies either. Limitation: inline `checkov:skip` comments are honored and can't be
disabled.

Severity (DATA_MODEL §4): reported severity when present; OSS checkov usually reports null →
`None` (rules treat unknown severity explicitly).
"""

import json
from pathlib import Path, PurePosixPath
from typing import Any, cast

from pydantic import JsonValue

from archlens.facts.base import ScanContext, SubprocessAdapter, evidence_or_scan, make_fact
from archlens.models import Fact, FileEntry, ScanEvidence, Severity

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
_LINK = "checkov-root"


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


def parse_error(
    path: str, framework: str, source: str, version: str, reason: str = "parse_error"
) -> Fact:
    """`iac_parse_error` fact for an IaC file checkov never checked: it could not parse it
    (`reason="parse_error"`) or skipped it under a hidden directory (`"hidden_directory"`).
    ScanEvidence only (no line)."""
    query = f"{reason.replace('_', ' ')} @ {path}"
    evidence = ScanEvidence(tool="checkov", tool_version=version, query=query, result_count=1)
    attributes: dict[str, JsonValue] = {"path": path, "framework": framework, "reason": reason}
    return make_fact("iac_parse_error", source, attributes, [evidence])


def hidden_iac(files: list[FileEntry]) -> list[str]:
    """Non-vendored IaC files checkov skips without a word: a path component starts with "."."""
    return sorted(
        f.path for f in files
        if f.readable and not f.is_vendored and is_iac(f.path)
        and any(part.startswith(".") for part in PurePosixPath(f.path).parts)
    )  # fmt: skip


def has_hidden_part(path: Path) -> bool:
    """True if a component of `path` starts with "." (what checkov's `/.` test matches)."""
    return any(part.startswith(".") for part in path.parts)


def scan_dir(ctx: ScanContext) -> Path:
    """Directory checkov scans: the snapshot root, or `<workdir>/checkov-root` (a symlink to it,
    made by `CheckovAdapter.target_args`) when the root has a hidden component."""
    return ctx.workdir / _LINK if has_hidden_part(ctx.root) else ctx.root


class CheckovAdapter(SubprocessAdapter):
    tool = "checkov"
    binary = "checkov"

    def targets(self, files: list[FileEntry]) -> list[str]:
        return [f.path for f in files if f.readable and not f.is_vendored and is_iac(f.path)]

    def target_args(self, ctx: ScanContext, targets: list[str]) -> list[str]:
        """None (the directory is in `options`). Makes the `scan_dir` symlink: here, because the
        runner records an OSError from this call as status error instead of raising."""
        directory = scan_dir(ctx)
        if has_hidden_part(directory):
            raise OSError("checkov skips files under hidden directories; the scratch dir has one")
        if directory != ctx.root:
            directory.unlink(missing_ok=True)
            directory.symlink_to(ctx.root, target_is_directory=True)
        return []

    def options(self, ctx: ScanContext) -> list[str]:
        return [
            f"--directory={scan_dir(ctx)}", "--framework", *FRAMEWORKS, "--skip-download",
            "--skip-results-upload", "--soft-fail", "-o", "json", "--compact",
        ]  # fmt: skip

    def parse(self, stdout: str, ctx: ScanContext, version: str) -> list[Fact]:
        data: Any = json.loads(stdout or "{}")
        blocks = cast(list[dict[str, Any]], data if isinstance(data, list) else [data])
        source = f"checkov@{version}"
        facts: list[Fact] = []
        for block in blocks:
            framework = str(block.get("check_type", ""))
            results = block.get("results", {})
            unparsed = [ctx.rel(str(p)) for p in results.get("parsing_errors", [])]
            if int(block.get("summary", {}).get("parsing_errors") or 0) > len(unparsed):
                raise ValueError(f"{framework}: checkov counted parse errors it did not list")
            facts += [parse_error(path, framework, source, version) for path in unparsed]
            for check in results.get("failed_checks", []):
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
        listed = {str(f.attributes["path"]) for f in facts if f.kind == "iac_parse_error"}
        facts += [
            parse_error(path, "", source, version, reason="hidden_directory")
            for path in hidden_iac(ctx.snapshot.files) if path not in listed
        ]  # fmt: skip
        return facts
