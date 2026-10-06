"""lizard (Python API) → `function_metrics` facts.

Call (lizard 1.24.1): `lizard.analyze_file(<absolute path>)` per source file. lizard tokenizes and
counts; it never imports or executes the analyzed code. The run stops with status `timeout` once
the tool's time budget is spent (an in-process call can't be killed mid-file).
"""

import time
from typing import Any, cast

import lizard  # pyright: ignore[reportMissingTypeStubs]
from pydantic import JsonValue

from archlens.facts.base import ScanContext, evidence_or_scan, make_fact
from archlens.models import Fact, FileEntry, ToolRunRecord, ToolStatus

LANGUAGES = frozenset(
    {"python", "javascript", "typescript", "java", "kotlin", "scala", "go", "csharp", "ruby", "php",
     "rust", "c", "cpp", "swift"}
)  # fmt: skip
VERSION = str(cast(Any, lizard).version)


def _functions(path: str) -> list[Any]:
    info: Any = cast(Any, lizard).analyze_file(path)
    return list(info.function_list)


class LizardAdapter:
    tool = "lizard"

    def targets(self, files: list[FileEntry]) -> list[str]:
        return [
            f.path
            for f in files
            if f.readable and not f.is_vendored and not f.is_generated and f.language in LANGUAGES
        ]

    def run(self, ctx: ScanContext) -> tuple[list[Fact], ToolRunRecord]:
        started = time.monotonic()
        targets = self.targets(ctx.snapshot.files)
        budget = ctx.tool(self.tool).timeout_s
        source = f"lizard@{VERSION}"

        def record(status: ToolStatus, facts: int = 0, error: str | None = None) -> ToolRunRecord:
            return ToolRunRecord(
                tool=self.tool,
                version=VERSION,
                command=["lizard.analyze_file", f"<{len(targets)} targets>"],
                status=status,
                duration_ms=int((time.monotonic() - started) * 1000),
                fact_count=facts,
                error=error,
            )

        if not targets:
            return [], record("skipped")
        facts: list[Fact] = []
        for path in targets:
            if time.monotonic() - started > budget:
                return [], record("timeout", error=f"time budget of {budget}s spent")
            try:
                functions = _functions(str(ctx.root / path))
            except Exception as exc:  # lizard raises arbitrary errors on odd input
                return [], record("error", error=f"{path}: {type(exc).__name__}: {exc}"[:500])
            for fn in functions:
                attributes: dict[str, JsonValue] = {
                    "name": str(fn.name),
                    "ccn": int(fn.cyclomatic_complexity),
                    "nloc": int(fn.nloc),
                    "params": len(fn.parameters),
                }
                line = int(fn.start_line)  # the signature line is enough evidence
                evidence = evidence_or_scan(
                    ctx, path, line, line, tool="lizard", version=VERSION, query=str(fn.name)
                )
                facts.append(make_fact("function_metrics", source, attributes, evidence))
        return facts, record("ok", facts=len(facts))
