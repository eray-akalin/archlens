"""Stage 1: scanners + extractors → FactSet (docs/ARCHITECTURE.md §2.1)."""

from dataclasses import dataclass
from pathlib import Path

from archlens.config import ToolsConfig
from archlens.facts.base import ScanContext
from archlens.facts.extractors import run_extractors
from archlens.facts.scanners import run_scanners
from archlens.models import FactSet, IngestLimits, RepoSnapshot
from archlens.security.redact import Redactor


@dataclass(frozen=True)
class FactsResult:
    facts: FactSet
    redactor: Redactor  # every secret span found; reuse it for all later reads of this snapshot


def collect_facts(
    root: Path,
    snapshot: RepoSnapshot,
    *,
    workdir: Path,
    tools: ToolsConfig,
    tools_dir: Path,
    limits: IngestLimits | None = None,
    scanners: bool = True,
) -> FactsResult:
    """Run every scanner (unless `scanners=False`) and extractor over one snapshot.

    Tool problems are recorded in `FactSet.tool_runs` with status error/timeout/skipped; this
    function only raises for programming errors.
    """
    limits = limits or IngestLimits()
    ctx = ScanContext.create(root, snapshot, workdir, tools, tools_dir, limits.max_file_bytes)
    facts, runs, redactor = [], [], Redactor()
    if scanners:
        results = run_scanners(ctx)
        facts, runs, redactor = list(results.facts), list(results.tool_runs), results.redactor
    extracted, extractor_runs = run_extractors(ctx.with_redactor(redactor))
    return FactsResult(
        facts=FactSet(
            commit_sha=snapshot.commit_sha, facts=facts + extracted, tool_runs=runs + extractor_runs
        ),
        redactor=redactor,
    )
