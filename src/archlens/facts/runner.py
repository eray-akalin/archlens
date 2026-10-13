"""Stage 1: scanners + extractors → FactSet (docs/ARCHITECTURE.md §2.1)."""

from dataclasses import dataclass
from pathlib import Path

from archlens.config import ToolsConfig
from archlens.facts.base import ScanContext
from archlens.facts.extractors import run_extractors
from archlens.facts.scanners import run_scanners
from archlens.models import CodeEvidence, Fact, FactSet, IngestLimits, RepoSnapshot
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
            commit_sha=snapshot.commit_sha,
            facts=project_facts([*facts, *extracted], snapshot),
            tool_runs=runs + extractor_runs,
        ),
        redactor=redactor,
    )


# Wherever they sit, a committed secret is a leak and an injection attempt is an attack.
KEPT_EVERYWHERE = frozenset({"secret", "injection_attempt"})


def project_facts(facts: list[Fact], snapshot: RepoSnapshot) -> list[Fact]:
    """Drop facts whose code evidence lies only in vendored or test-data files (`is_vendored`):
    a scanner finding in a fixture says nothing about the project. Facts without code evidence,
    and the kinds in KEPT_EVERYWHERE, always stay."""
    vendored = {f.path for f in snapshot.files if f.is_vendored}
    if not vendored:
        return facts

    def keep(fact: Fact) -> bool:
        paths = [e.path for e in fact.evidence if isinstance(e, CodeEvidence)]
        return fact.kind in KEPT_EVERYWHERE or not paths or any(p not in vendored for p in paths)

    return [fact for fact in facts if keep(fact)]
