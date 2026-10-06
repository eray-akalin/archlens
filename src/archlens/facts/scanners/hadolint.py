"""hadolint → `hadolint_finding` facts.

Command (hadolint 2.15.1)::

    hadolint --config <work>/hadolint.yaml --no-fail --no-color --disable-ignore-pragma
        -f json <Dockerfile> ...

Parses Dockerfiles; never builds or runs them. Our config is passed explicitly and the tool runs
from a scratch cwd with a throwaway HOME, so no repo `.hadolint.yaml` applies;
`--disable-ignore-pragma` ignores inline `# hadolint ignore=` comments.

Severity (DATA_MODEL §4): error → high, warning → medium, info/style → low.
"""

import json

from pydantic import JsonValue

from archlens.facts.base import ScanContext, SubprocessAdapter, evidence_or_scan, make_fact
from archlens.models import Fact, FileEntry, Severity

_LEVEL: dict[str, Severity] = {"error": "high", "warning": "medium", "info": "low", "style": "low"}


class HadolintAdapter(SubprocessAdapter):
    tool = "hadolint"
    binary = "hadolint"

    def targets(self, files: list[FileEntry]) -> list[str]:
        return [f.path for f in files if f.language == "dockerfile" and f.readable]

    def options(self, ctx: ScanContext) -> list[str]:
        config = ctx.workdir / "hadolint.yaml"
        config.write_text("ignored: []\n")  # hadolint rejects an empty file
        return [
            "--config",
            str(config),
            "--no-fail",
            "--no-color",
            "--disable-ignore-pragma",
            "-f",
            "json",
        ]

    def parse(self, stdout: str, ctx: ScanContext, version: str) -> list[Fact]:
        source = f"hadolint@{version}"
        facts: list[Fact] = []
        for item in json.loads(stdout or "[]"):
            path = ctx.rel(item["file"])
            line = int(item["line"])
            attributes: dict[str, JsonValue] = {
                "code": item["code"],
                "level": item["level"],
                "message": str(item.get("message", ""))[:500],
            }
            evidence = evidence_or_scan(
                ctx, path, line, line, tool="hadolint", version=version, query=item["code"]
            )
            facts.append(
                make_fact(
                    "hadolint_finding", source, attributes, evidence, _LEVEL.get(item["level"])
                )
            )
        return facts
