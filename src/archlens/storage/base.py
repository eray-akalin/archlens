"""Storage protocols shared by the local (M0.6) and Azure (M4.2) backends.

All methods are async (IO-bound). Every identifier is validated before it reaches a path, blob
name or document ID: artifact names come from API URLs, so this is a path-traversal boundary.
"""

import re
from dataclasses import dataclass
from typing import Protocol

from archlens.errors import StorageKeyError
from archlens.models import RunState

_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_CHECKPOINT_KEY = re.compile(
    r"^[a-z0-9_]{1,64}(/[A-Za-z0-9_.-]{1,64})?$"
)  # "facts", "evaluate/security"
_NAMESPACE = re.compile(r"^[a-z0-9_]{1,32}$")
_CACHE_KEY = re.compile(r"^[A-Za-z0-9:_.-]{1,256}$")


def _check(pattern: re.Pattern[str], kind: str, value: str) -> str:
    if not pattern.fullmatch(value):
        raise StorageKeyError(f"invalid {kind}: {value!r}")
    return value


def check_run_id(value: str) -> str:
    return _check(_RUN_ID, "run_id", value)


def check_artifact_name(value: str) -> str:
    return _check(_ARTIFACT_NAME, "artifact name", value)


def check_checkpoint_key(value: str) -> str:
    return _check(_CHECKPOINT_KEY, "checkpoint key", value)


def check_cache_key(namespace: str, key: str) -> tuple[str, str]:
    return _check(_NAMESPACE, "cache namespace", namespace), _check(_CACHE_KEY, "cache key", key)


class ArtifactStore(Protocol):
    """Run outputs (assessment.json, report.md, ...). Missing → None, never an exception."""

    async def put(self, run_id: str, name: str, data: bytes) -> None: ...
    async def get(self, run_id: str, name: str) -> bytes | None: ...
    async def names(self, run_id: str) -> list[str]: ...


class RunStateStore(Protocol):
    async def save(self, state: RunState) -> None: ...
    async def load(self, run_id: str) -> RunState | None: ...


class CacheStore(Protocol):
    """Content-addressed cache (LLM exact cache, metric results, embeddings).

    Expiry is checked against the store's clock on read, so it behaves the same on every backend.
    """

    async def get(self, namespace: str, key: str) -> bytes | None: ...
    async def put(
        self, namespace: str, key: str, value: bytes, ttl_s: int | None = None
    ) -> None: ...


class CheckpointStore(Protocol):
    """Stage outputs for `--resume`; key is a stage, or `evaluate/<metric>` per metric."""

    async def save(self, run_id: str, key: str, data: bytes) -> None: ...
    async def load(self, run_id: str, key: str) -> bytes | None: ...
    async def keys(self, run_id: str) -> list[str]: ...


@dataclass(frozen=True)
class Storage:
    artifacts: ArtifactStore
    run_state: RunStateStore
    cache: CacheStore
    checkpoints: CheckpointStore
