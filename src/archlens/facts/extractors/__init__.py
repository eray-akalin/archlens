"""Deterministic AST/filesystem extractors (docs/ARCHITECTURE.md §2.1).

Each extractor is a function `(ctx) -> list[Fact]` registered under its source name. The runner
records one ToolRunRecord per extractor; an exception becomes status `error` (rules then answer
`unknown`), never a crashed run.
"""

import logging
import time
from collections.abc import Callable

from archlens import __version__
from archlens.facts.base import ScanContext
from archlens.facts.extractors.ci import extract_ci
from archlens.facts.extractors.deploy import extract_deploy_configs
from archlens.facts.extractors.docker import extract_dockerfiles
from archlens.facts.extractors.docs import extract_doc_files
from archlens.facts.extractors.imports import extract_imports
from archlens.facts.extractors.logging_ import extract_log_calls
from archlens.facts.extractors.manifests import extract_manifests
from archlens.facts.extractors.metrics import extract_file_metrics
from archlens.facts.extractors.routes import extract_routes
from archlens.facts.extractors.tests import extract_test_files
from archlens.models import Fact, ToolRunRecord, ToolStatus

logger = logging.getLogger(__name__)

Extractor = Callable[[ScanContext], list[Fact]]

EXTRACTORS: dict[str, Extractor] = {
    "ast:imports": extract_imports,
    "ast:manifests": extract_manifests,
    "ast:ci": extract_ci,
    "ast:docker": extract_dockerfiles,
    "ast:deploy": extract_deploy_configs,
    "ast:routes": extract_routes,
    "ast:tests": extract_test_files,
    "ast:logging": extract_log_calls,
    "ast:metrics": extract_file_metrics,
    "fs:docs": extract_doc_files,
}


def run_extractors(ctx: ScanContext) -> tuple[list[Fact], list[ToolRunRecord]]:
    facts: list[Fact] = []
    runs: list[ToolRunRecord] = []
    for name, extractor in EXTRACTORS.items():
        started = time.monotonic()
        status: ToolStatus = "ok"
        error: str | None = None
        found: list[Fact] = []
        try:
            found = extractor(ctx)
        except Exception as exc:  # one broken extractor must not fail the run
            logger.exception("extractor %s failed", name)
            status, error = "error", f"{type(exc).__name__}: {exc}"[:500]
        facts += found
        runs.append(
            ToolRunRecord(
                tool=name,
                version=__version__,
                command=[name],
                status=status,
                duration_ms=int((time.monotonic() - started) * 1000),
                fact_count=len(found),
                error=error,
            )
        )
    return facts, runs
