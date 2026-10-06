"""Local backend: files and SQLite under ARCHLENS_DATA_DIR (docs/ARCHITECTURE.md §6).

Layout::

    <data_dir>/cache.sqlite
    <data_dir>/runs/<run_id>/state.json
    <data_dir>/runs/<run_id>/artifacts/<name>
    <data_dir>/runs/<run_id>/checkpoints/<stage>.json | <stage>/<metric>.json

Writes go to a temp file in the same directory and are renamed into place, so a crash never
leaves a half-written checkpoint for `--resume` to read.
"""

import asyncio
import os
import sqlite3
import tempfile
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from archlens.models import RunState
from archlens.storage.base import (
    Storage,
    check_artifact_name,
    check_cache_key,
    check_checkpoint_key,
    check_run_id,
)

Clock = Callable[[], float]
_SUFFIX = ".json"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _run_dir(root: Path, run_id: str) -> Path:
    return root / "runs" / check_run_id(run_id)


class LocalArtifactStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def _dir(self, run_id: str) -> Path:
        return _run_dir(self._root, run_id) / "artifacts"

    async def put(self, run_id: str, name: str, data: bytes) -> None:
        await asyncio.to_thread(_atomic_write, self._dir(run_id) / check_artifact_name(name), data)

    async def get(self, run_id: str, name: str) -> bytes | None:
        return await asyncio.to_thread(_read, self._dir(run_id) / check_artifact_name(name))

    async def names(self, run_id: str) -> list[str]:
        directory = self._dir(run_id)

        def scan() -> list[str]:
            if not directory.is_dir():
                return []
            return sorted(p.name for p in directory.iterdir() if not p.name.startswith("."))

        return await asyncio.to_thread(scan)


class LocalRunStateStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    async def save(self, state: RunState) -> None:
        path = _run_dir(self._root, state.run_id) / "state.json"
        await asyncio.to_thread(_atomic_write, path, state.model_dump_json(indent=2).encode())

    async def load(self, run_id: str) -> RunState | None:
        data = await asyncio.to_thread(_read, _run_dir(self._root, run_id) / "state.json")
        return None if data is None else RunState.model_validate_json(data)


class LocalCheckpointStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def _dir(self, run_id: str) -> Path:
        return _run_dir(self._root, run_id) / "checkpoints"

    async def save(self, run_id: str, key: str, data: bytes) -> None:
        path = self._dir(run_id) / (check_checkpoint_key(key) + _SUFFIX)
        await asyncio.to_thread(_atomic_write, path, data)

    async def load(self, run_id: str, key: str) -> bytes | None:
        path = self._dir(run_id) / (check_checkpoint_key(key) + _SUFFIX)
        return await asyncio.to_thread(_read, path)

    async def keys(self, run_id: str) -> list[str]:
        directory = self._dir(run_id)

        def scan() -> list[str]:
            if not directory.is_dir():
                return []
            return sorted(
                p.relative_to(directory).as_posix().removesuffix(_SUFFIX)
                for p in directory.rglob(f"*{_SUFFIX}")
                if not p.name.startswith(".")
            )

        return await asyncio.to_thread(scan)


class SqliteCacheStore:
    def __init__(self, path: Path, clock: Clock = time.time) -> None:
        self._path = path
        self._clock = clock
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cache ("
                " namespace TEXT NOT NULL, key TEXT NOT NULL, value BLOB NOT NULL,"
                " expires_at REAL, PRIMARY KEY (namespace, key))"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=30)

    def _get(self, namespace: str, key: str) -> bytes | None:
        with closing(self._connect()) as conn, conn:
            row: tuple[bytes, float | None] | None = conn.execute(
                "SELECT value, expires_at FROM cache WHERE namespace = ? AND key = ?",
                (namespace, key),
            ).fetchone()
            if row is None:
                return None
            value, expires_at = row
            if expires_at is not None and expires_at <= self._clock():
                conn.execute("DELETE FROM cache WHERE namespace = ? AND key = ?", (namespace, key))
                return None
            return value

    def _put(self, namespace: str, key: str, value: bytes, ttl_s: int | None) -> None:
        expires_at = None if ttl_s is None else self._clock() + ttl_s
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (namespace, key, value, expires_at)"
                " VALUES (?, ?, ?, ?)",
                (namespace, key, value, expires_at),
            )

    async def get(self, namespace: str, key: str) -> bytes | None:
        namespace, key = check_cache_key(namespace, key)
        return await asyncio.to_thread(self._get, namespace, key)

    async def put(self, namespace: str, key: str, value: bytes, ttl_s: int | None = None) -> None:
        namespace, key = check_cache_key(namespace, key)
        await asyncio.to_thread(self._put, namespace, key, value, ttl_s)


def open_local_storage(data_dir: Path, *, clock: Clock = time.time) -> Storage:
    """All four stores rooted at `data_dir` (created on first write)."""
    return Storage(
        artifacts=LocalArtifactStore(data_dir),
        run_state=LocalRunStateStore(data_dir),
        cache=SqliteCacheStore(data_dir / "cache.sqlite", clock=clock),
        checkpoints=LocalCheckpointStore(data_dir),
    )
