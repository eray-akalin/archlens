"""The eval set (`eval/repos.yaml`, EVALUATION.md §1): public repositories pinned to a commit and
cloned at eval time into a local cache — never vendored into this repository.

`fetch` uses the hardened clone (SECURITY.md §3) at the pinned SHA, so ingest limits apply before
checkout, and is idempotent: a cached checkout of the same commit is reused.
"""

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import Field, ValidationError, model_validator

from archlens.errors import ConfigError, IngestError
from archlens.ingest.clone import clone_repo
from archlens.models import Contract, IngestLimits

DEFAULT_REPOS_FILE = Path("eval/repos.yaml")
MARKER_SUFFIX = ".commit"  # next to the checkout, so the assessed tree stays untouched
_SHA = r"^[0-9a-f]{40}$"


class EvalRepo(Contract):
    id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]*$")]
    url: str
    commit: Annotated[str, Field(pattern=_SHA)]
    license: str
    langs: list[str]
    notes: str = ""


class EvalRepos(Contract):
    repos: list[EvalRepo]

    @model_validator(mode="after")
    def _unique_ids(self) -> "EvalRepos":
        ids = [r.id for r in self.repos]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate repo ids: {ids}")
        return self


@dataclass(frozen=True)
class Fetched:
    repo: EvalRepo
    path: Path  # the checkout (working tree)
    cached: bool  # True when an earlier fetch was reused


def load_repos(path: Path = DEFAULT_REPOS_FILE) -> list[EvalRepo]:
    """Parse and validate the eval set. Raises ConfigError."""
    try:
        data: object = yaml.safe_load(path.read_text(encoding="utf-8"))
        return EvalRepos.model_validate(data).repos
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(str(path), "repos", str(exc)) from exc
    except ValidationError as exc:
        raise ConfigError(str(path), "repos", str(exc)) from exc


def checkout_path(repo: EvalRepo, cache_dir: Path) -> Path:
    return cache_dir / f"{repo.id}-{repo.commit[:12]}"


def marker_path(checkout: Path) -> Path:
    return checkout.with_name(checkout.name + MARKER_SUFFIX)


def fetch(
    repo: EvalRepo,
    cache_dir: Path,
    *,
    limits: IngestLimits | None = None,
    protocol: str = "https",
) -> Fetched:
    """Clone `repo` at its pinned commit into `cache_dir` (or reuse the cached checkout).

    Raises IngestError (clone failure, limits exceeded, commit mismatch).
    """
    dest = checkout_path(repo, cache_dir)
    marker = marker_path(dest)
    if marker.is_file() and marker.read_text().strip() == repo.commit:
        return Fetched(repo, dest, cached=True)
    if dest.exists():
        shutil.rmtree(dest)  # a partial or stale checkout of ours
    marker.unlink(missing_ok=True)
    work = cache_dir / f".tmp-{repo.id}-{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True)
    try:
        cloned = clone_repo(
            repo.url, repo.commit, work, limits or IngestLimits(), protocol=protocol
        )
        if cloned.commit_sha != repo.commit:
            raise IngestError(f"{repo.id}: cloned {cloned.commit_sha}, pinned {repo.commit}")
        cloned.root.rename(dest)
        marker.write_text(repo.commit + "\n")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return Fetched(repo, dest, cached=False)
