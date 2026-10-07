"""osv-scanner → `vuln_dependency` facts.

Command (osv-scanner 2.6.0)::

    osv-scanner scan source --recursive --no-resolve --allow-no-lockfiles --no-ignore
        --config <work>/osv-scanner.toml --format json --verbosity error <repo>

`--no-ignore`: without it osv-scanner honours `.gitignore` files, including those of a repository
that merely contains the snapshot (e.g. a checkout under a gitignored data dir), and silently
reports nothing. Results are kept only for non-vendored files listed in the snapshot, so the
snapshot alone decides the scope (as for semgrep's `--no-git-ignore`).

Reads lockfiles and manifests as text; never executes repo code. `--no-resolve` disables
transitive resolution through package registries, and `--call-analysis` is never enabled (its
help says it runs build scripts). `--config` points at our empty config, so a repo
`osv-scanner.toml` can't ignore vulnerabilities. Network: package names and versions (not code)
are sent to the OSV API.

Severity (DATA_MODEL §4): highest CVSS score of the advisory group → critical ≥ 9, high ≥ 7,
medium ≥ 4, low > 0; else the GitHub advisory label; else medium with severity_source="default".
"""

import json
from typing import Any

from pydantic import JsonValue

from archlens.facts.base import ScanContext, SubprocessAdapter, evidence_or_scan, make_fact
from archlens.models import Fact, FileEntry, Severity

MANIFESTS = frozenset(
    {
        "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock",
        "poetry.lock", "uv.lock", "pdm.lock", "Pipfile.lock", "pylock.toml",
        "go.mod", "Cargo.lock", "composer.lock", "Gemfile.lock", "mix.lock", "pubspec.lock",
        "packages.lock.json", "gradle.lockfile", "buildscript-gradle.lockfile", "pom.xml",
        "conan.lock", "renv.lock",
    }
)  # fmt: skip
_RANK: dict[Severity, int] = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
_GHSA_LABELS: dict[str, Severity] = {
    "CRITICAL": "critical",
    "HIGH": "high",
    "MODERATE": "medium",
    "LOW": "low",
}


def is_manifest(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name in MANIFESTS or (name.startswith("requirements") and name.endswith(".txt"))


def cvss_to_severity(score: float) -> Severity | None:
    if score >= 9.0:
        return "critical"
    if score >= 7.0:
        return "high"
    if score >= 4.0:
        return "medium"
    if score > 0:
        return "low"
    return None


def group_severity(group: dict[str, Any], vulns: list[dict[str, Any]]) -> tuple[Severity, str]:
    """(severity, source) for one advisory group."""
    try:
        from_score = cvss_to_severity(float(group.get("max_severity") or 0))
    except ValueError:
        from_score = None
    if from_score:
        return from_score, "cvss"
    ids = set(group.get("ids", []))
    labels: list[Severity] = []
    for vuln in vulns:
        label = str(vuln.get("database_specific", {}).get("severity", "")).upper()
        if vuln.get("id") in ids and label in _GHSA_LABELS:
            labels.append(_GHSA_LABELS[label])
    if labels:
        return min(labels, key=_RANK.__getitem__), "advisory"
    return "medium", "default"


class OsvAdapter(SubprocessAdapter):
    tool = "osv-scanner"
    binary = "osv-scanner"
    ok_exit_codes = frozenset({0, 1})  # 1 = vulnerabilities found

    def targets(self, files: list[FileEntry]) -> list[str]:
        return [f.path for f in files if is_manifest(f.path) and f.symlink_target is None]

    def target_args(self, ctx: ScanContext, targets: list[str]) -> list[str]:
        return [str(ctx.root)]

    def options(self, ctx: ScanContext) -> list[str]:
        config = ctx.workdir / "osv-scanner.toml"
        config.write_text("")
        return [
            "scan", "source", "--recursive", "--no-resolve", "--allow-no-lockfiles", "--no-ignore",
            "--config", str(config), "--format", "json", "--verbosity", "error",
        ]  # fmt: skip

    def parse(self, stdout: str, ctx: ScanContext, version: str) -> list[Fact]:
        facts: list[Fact] = []
        source = f"osv-scanner@{version}"
        in_scope = {f.path for f in ctx.snapshot.files if not f.is_vendored}
        for result in json.loads(stdout or "{}").get("results", []):
            path = ctx.rel(result["source"]["path"])
            if path not in in_scope:
                continue
            for pkg in result.get("packages", []):
                info = pkg["package"]
                vulns: list[dict[str, Any]] = pkg.get("vulnerabilities", [])
                for group in pkg.get("groups", []):
                    severity, severity_source = group_severity(group, vulns)
                    ids = sorted(str(i) for i in group.get("ids", []))
                    attributes: dict[str, JsonValue] = {
                        "package": info["name"],
                        "version": info.get("version", ""),
                        "ecosystem": info.get("ecosystem", ""),
                        "ids": list[JsonValue](ids),
                        "max_severity": severity,
                        "severity_source": severity_source,
                        "cvss": str(group.get("max_severity") or ""),
                    }
                    line = self._declaration_line(ctx, path, str(info["name"]))
                    query = ids[0] if ids else str(info["name"])
                    evidence = evidence_or_scan(
                        ctx, path, line, line, tool="osv-scanner", version=version, query=query
                    )
                    facts.append(
                        make_fact("vuln_dependency", source, attributes, evidence, severity)
                    )
        return facts

    @staticmethod
    def _declaration_line(ctx: ScanContext, path: str, package: str) -> int:
        """1-based line where the package is declared in the manifest (1 if not found)."""
        needle = package.lower()
        for number, line in enumerate(ctx.reader.lines(path) or [], start=1):
            if needle in line.lower().replace("_", "-") or needle in line.lower():
                return number
        return 1
