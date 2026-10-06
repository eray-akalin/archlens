"""actionlint → `actionlint_finding` facts.

Command (actionlint 1.7.12)::

    actionlint -config-file <work>/actionlint.yaml -no-color -shellcheck= -pyflakes=
        -format '{{json .}}' <workflow> ...

Parses workflow YAML; never runs workflows or their scripts. Our config file is passed explicitly,
so a repo `.github/actionlint.yaml` (which could ignore errors) is never read; the
shellcheck/pyflakes integrations are disabled so results don't depend on what else is installed.
Runs with cwd=<repo> because actionlint reports paths relative to its cwd (verified on 1.7.12:
absolute targets come back as `../../...` from a scratch cwd).
"""

import json
from pathlib import Path

from pydantic import JsonValue

from archlens.facts.base import ScanContext, SubprocessAdapter, evidence_or_scan, make_fact
from archlens.models import Fact, FileEntry
from archlens.security.redact import redact


def is_workflow(path: str) -> bool:
    return path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml"))


class ActionlintAdapter(SubprocessAdapter):
    tool = "actionlint"
    binary = "actionlint"
    version_args = ("-version",)
    ok_exit_codes = frozenset({0, 1})  # 1 = problems found

    def targets(self, files: list[FileEntry]) -> list[str]:
        return [f.path for f in files if is_workflow(f.path) and f.readable]

    def cwd(self, ctx: ScanContext) -> Path:
        return ctx.root

    def options(self, ctx: ScanContext) -> list[str]:
        config = ctx.workdir / "actionlint.yaml"
        config.write_text("")
        return [
            "-config-file",
            str(config),
            "-no-color",
            "-shellcheck=",
            "-pyflakes=",
            "-format",
            "{{json .}}",
        ]

    def parse(self, stdout: str, ctx: ScanContext, version: str) -> list[Fact]:
        source = f"actionlint@{version}"
        facts: list[Fact] = []
        for item in json.loads(stdout or "[]"):
            path = ctx.rel(item["filepath"])
            line = int(item.get("line") or 1)
            attributes: dict[str, JsonValue] = {
                "kind": str(item.get("kind", "")),
                "message": redact(str(item.get("message", "")))[:500],
            }
            evidence = evidence_or_scan(
                ctx,
                path,
                line,
                line,
                tool="actionlint",
                version=version,
                query=str(item.get("kind", "")),
            )
            facts.append(make_fact("actionlint_finding", source, attributes, evidence))
        return facts
