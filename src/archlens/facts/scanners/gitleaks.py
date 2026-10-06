"""gitleaks → `secret` facts.

Command (gitleaks 8.30.1)::

    gitleaks dir <repo> --config <work>/gitleaks.toml --gitleaks-ignore-path <work>/gitleaks-empty
        --ignore-gitleaks-allow --redact --no-banner --exit-code 0 --log-level error
        --report-format json --report-path -

Reads files only; never executes repo code. A hostile repo can't suppress findings: our config
extends the built-in rules (so a repo `.gitleaks.toml` is not loaded), the ignore path is an empty
directory (no `.gitleaksignore`), and `gitleaks:allow` comments are ignored. `--redact` keeps
values out of the report; facts store rule, fingerprint and location only.

Column quirk (verified on 8.30.1): StartColumn/EndColumn are correct 1-based columns on line 1 but
one too high on later lines (the preceding newline is counted); `corrected_column` fixes that.
"""

import json

from pydantic import JsonValue

from archlens.facts.base import ScanContext, SubprocessAdapter, evidence_or_scan, make_fact
from archlens.models import Fact, FileEntry
from archlens.security.redact import Redactor, SecretSpan

_CONFIG = "[extend]\nuseDefault = true\n"


def corrected_column(line: int, column: int) -> int:
    return column - 1 if line > 1 else column


def secret_span(attributes: dict[str, JsonValue]) -> SecretSpan:
    """The Redactor span described by a `secret` fact's location attributes."""
    return SecretSpan(
        path=str(attributes["path"]),
        start_line=int(str(attributes["start_line"])),
        start_col=int(str(attributes["start_col"])),
        end_line=int(str(attributes["end_line"])),
        end_col=int(str(attributes["end_col"])),
        rule_id=str(attributes["rule_id"]),
    )


def spans_from_facts(facts: list[Fact]) -> list[SecretSpan]:
    return [secret_span(f.attributes) for f in facts if f.kind == "secret"]


class GitleaksAdapter(SubprocessAdapter):
    tool = "gitleaks"
    binary = "gitleaks"
    version_args = ("version",)

    def targets(self, files: list[FileEntry]) -> list[str]:
        return ["."] if files else []

    def target_args(self, ctx: ScanContext, targets: list[str]) -> list[str]:
        return [str(ctx.root)]

    def options(self, ctx: ScanContext) -> list[str]:
        config = ctx.workdir / "gitleaks.toml"
        config.write_text(_CONFIG)
        empty = ctx.workdir / "gitleaks-empty"
        empty.mkdir(exist_ok=True)
        return [
            "dir",
            "--config", str(config),
            "--gitleaks-ignore-path", str(empty),
            "--ignore-gitleaks-allow",
            "--redact",
            "--no-banner",
            "--exit-code", "0",
            "--log-level", "error",
            "--report-format", "json",
            "--report-path", "-",
        ]  # fmt: skip

    def parse(self, stdout: str, ctx: ScanContext, version: str) -> list[Fact]:
        known = {f.path for f in ctx.snapshot.files}
        rows: list[tuple[str, dict[str, JsonValue]]] = []
        for item in json.loads(stdout or "[]"):
            path = ctx.rel(item["File"])
            if path not in known:  # e.g. files under .git/
                continue
            start, end = int(item["StartLine"]), int(item["EndLine"])
            attributes: dict[str, JsonValue] = {
                "rule_id": item["RuleID"],
                "fingerprint": f"{path}:{item['RuleID']}:{start}",
                "path": path,
                "start_line": start,
                "start_col": corrected_column(start, int(item["StartColumn"])),
                "end_line": end,
                "end_col": corrected_column(end, int(item["EndColumn"])),
            }
            rows.append((path, attributes))
        # Evidence for a secret is redacted with the spans of every secret found in this run.
        scoped = ctx.with_redactor(Redactor([secret_span(a) for _, a in rows]))
        source = f"gitleaks@{version}"
        facts: list[Fact] = []
        for path, a in rows:
            start, end, rule = int(str(a["start_line"])), int(str(a["end_line"])), str(a["rule_id"])
            evidence = evidence_or_scan(
                scoped, path, start, end, tool="gitleaks", version=version, query=rule
            )
            facts.append(make_fact("secret", source, a, evidence))
        return facts
