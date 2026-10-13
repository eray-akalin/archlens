"""Metric result cache (ARCHITECTURE.md §5): PR mode re-evaluates only the metrics whose inputs
changed.

The key covers what one metric's LLM evaluation depends on: the rubric version and the applicable
LLM checks, the evaluator prompt versions, the evaluator / tie-break / embedding settings, the
files matching the rubric's `scope_globs` (path, size, content hash) and the facts its checks are
given (`fact_kinds`). Deterministic checks are not cached: they are pure, cheap, and always re-run.
"""

import hashlib
import json
from collections.abc import Sequence

from pydantic import JsonValue

from archlens.config import ModelsConfig
from archlens.globs import glob_match
from archlens.models import CheckResult, CheckSpec, Contract, FactSet, FileEntry, Rubric

NAMESPACE = "metric_results"
FORMAT = 1  # bump when the key or the cached payload changes shape
FILES_NAMESPACE = "file_hashes"  # commit → {path: sha256}, so a later run can diff against it
FILES_TTL_S = 30 * 24 * 3600


class CachedMetric(Contract):
    """The LLM part of one metric's evaluation, plus its sessions' seen lines (the verifier
    rejects citations to lines a session never saw)."""

    results: list[CheckResult]
    ledger: dict[str, JsonValue]


class FileHashes(Contract):
    """A snapshot's listing reduced to content hashes (PR mode: which files a change touched)."""

    files: dict[str, str]  # path → sha256 ("" when the file was too large to read)

    @classmethod
    def of(cls, files: Sequence[FileEntry]) -> "FileHashes":
        return cls(files={f.path: f.sha256 or f"size:{f.size}" for f in files})

    def changed(self, other: "FileHashes") -> set[str]:
        """Paths added, removed or modified between self and `other`."""
        paths = self.files.keys() | other.files.keys()
        return {p for p in paths if self.files.get(p) != other.files.get(p)}


def metric_inputs_key(
    rubric: Rubric,
    checks: Sequence[CheckSpec],
    *,
    prompt_version: str,
    models: ModelsConfig,
    files: Sequence[FileEntry],
    facts: FactSet,
) -> str:
    """sha256 over the metric's inputs; equal keys mean the cached LLM results still apply.
    `checks` are the metric's applicable LLM checks (the profile flags decide which)."""
    kinds = sorted({kind for check in checks for kind in check.fact_kinds})
    roles = {name: role.model_dump() for name in ("evaluator", "tiebreak", "embed")
             if (role := models.roles.get(name)) is not None}  # fmt: skip
    payload = {
        "format": FORMAT,
        "metric": rubric.metric,
        "rubric_version": rubric.version,
        "checks": sorted(check.id for check in checks),
        "prompt_version": prompt_version,
        "roles": roles,
        "session": models.sessions.evaluator.model_dump(),
        "files": sorted(
            (f.path, f.size, f.sha256) for f in files if glob_match(f.path, rubric.scope_globs)
        ),
        "facts": sorted(fact.id for fact in facts.by_kind(*kinds)),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()
