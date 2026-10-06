"""Scanner adapters and the runner that executes them for one snapshot.

gitleaks runs first: its secret locations feed the Redactor used for every other adapter's
evidence. The remaining adapters run concurrently (they are mostly external processes).
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol

from archlens.facts.base import ScanContext
from archlens.facts.scanners.actionlint import ActionlintAdapter
from archlens.facts.scanners.checkov import CheckovAdapter
from archlens.facts.scanners.gitleaks import GitleaksAdapter, spans_from_facts
from archlens.facts.scanners.hadolint import HadolintAdapter
from archlens.facts.scanners.lizard_ import LizardAdapter
from archlens.facts.scanners.osv import OsvAdapter
from archlens.facts.scanners.semgrep import SemgrepAdapter
from archlens.models import Fact, ToolRunRecord
from archlens.security.redact import Redactor

__all__ = ["Adapter", "ScanResults", "default_adapters", "run_scanners"]


class Adapter(Protocol):
    def run(self, ctx: ScanContext) -> tuple[list[Fact], ToolRunRecord]: ...


@dataclass(frozen=True)
class ScanResults:
    facts: list[Fact]
    tool_runs: list[ToolRunRecord]
    redactor: Redactor  # includes every secret span found; reuse it for all later reads


def default_adapters() -> list[Adapter]:
    return [
        GitleaksAdapter(),
        OsvAdapter(),
        SemgrepAdapter(),
        HadolintAdapter(),
        CheckovAdapter(),
        ActionlintAdapter(),
        LizardAdapter(),
    ]


def run_scanners(
    ctx: ScanContext, adapters: list[Adapter] | None = None, max_workers: int = 4
) -> ScanResults:
    """Run adapters (default: all) and collect facts and one ToolRunRecord per adapter.

    Never raises for tool problems; they are recorded as error/timeout statuses.
    """
    adapters = adapters if adapters is not None else default_adapters()
    first = [a for a in adapters if isinstance(a, GitleaksAdapter)]
    rest = [a for a in adapters if not isinstance(a, GitleaksAdapter)]
    facts: list[Fact] = []
    runs: list[ToolRunRecord] = []
    for adapter in first:
        found, record = adapter.run(ctx)
        facts += found
        runs.append(record)
    redactor = Redactor(spans_from_facts(facts))
    scoped = ctx.with_redactor(redactor)

    def run_one(adapter: Adapter) -> tuple[list[Fact], ToolRunRecord]:
        return adapter.run(scoped)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for found, record in pool.map(run_one, rest):
            facts += found
            runs.append(record)
    return ScanResults(facts=facts, tool_runs=runs, redactor=redactor)
