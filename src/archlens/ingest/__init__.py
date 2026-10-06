"""Stage 0: turn a repository URL or local path into a RepoSnapshot (docs/ARCHITECTURE.md §2.0)."""

from dataclasses import dataclass
from pathlib import Path

from archlens.errors import IngestError
from archlens.ingest.clone import clone_repo
from archlens.ingest.snapshot import build_snapshot
from archlens.models import IngestLimits, RepoSnapshot

__all__ = ["Ingested", "ingest", "is_remote"]


@dataclass(frozen=True)
class Ingested:
    snapshot: RepoSnapshot
    root: Path  # kept in RunContext, never serialized


def is_remote(target: str) -> bool:
    return "://" in target or target.startswith("git@")


def ingest(
    target: str,
    *,
    workdir: Path,
    ref: str | None = None,
    limits: IngestLimits | None = None,
) -> Ingested:
    """Snapshot a remote repository (hardened clone into `workdir`) or a local directory.

    Local directories are read in place and never modified; `ref` applies to remotes only.
    Raises IngestError / IngestLimitExceeded.
    """
    limits = limits or IngestLimits()
    if is_remote(target):
        cloned = clone_repo(target, ref, workdir, limits)
        snapshot = build_snapshot(
            cloned.root, limits=limits, repo_url=target, ref=ref, commit_sha=cloned.commit_sha
        )
        return Ingested(snapshot=snapshot, root=cloned.root)
    if ref is not None:
        raise IngestError("a ref can only be requested for remote repositories")
    root = Path(target).resolve()
    return Ingested(snapshot=build_snapshot(root, limits=limits), root=root)
