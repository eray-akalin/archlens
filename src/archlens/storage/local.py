"""Local backend: files and SQLite under ARCHLENS_DATA_DIR (docs/ARCHITECTURE.md §6).

Layout::

    <data_dir>/cache.sqlite
    <data_dir>/runs/<run_id>/state.json
    <data_dir>/runs/<run_id>/artifacts/<name>
    <data_dir>/runs/<run_id>/checkpoints/<stage>.json | <stage>/<metric>.json
    <data_dir>/jobs/<run_id>.json
    <data_dir>/queue/<message_id>.json   (+ .lock: receive/delete hold an exclusive flock)

Writes go to a temp file in the same directory and are renamed into place, so a crash never
leaves a half-written checkpoint for `--resume` to read.
"""

import asyncio
import fcntl
import os
import sqlite3
import tempfile
import time
from collections.abc import Callable, Generator
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

from ulid import ULID

from archlens.errors import StorageKeyError
from archlens.models import Job, MutableContract, RunState
from archlens.storage.base import (
    QueueMessage,
    Storage,
    check_artifact_name,
    check_cache_key,
    check_checkpoint_key,
    check_key_id,
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


class LocalJobStore:
    def __init__(self, root: Path) -> None:
        self._dir = root / "jobs"

    async def create(self, job: Job) -> None:
        path = self._dir / f"{check_run_id(job.run_id)}{_SUFFIX}"
        await asyncio.to_thread(_atomic_write, path, job.model_dump_json().encode())

    async def get(self, run_id: str) -> Job | None:
        data = await asyncio.to_thread(_read, self._dir / f"{check_run_id(run_id)}{_SUFFIX}")
        return Job.model_validate_json(data) if data is not None else None

    async def by_key(self, key_id: str, since: datetime) -> list[Job]:
        check_key_id(key_id)

        def scan() -> list[Job]:
            if not self._dir.is_dir():
                return []
            jobs = (Job.model_validate_json(p.read_bytes()) for p in self._dir.glob(f"*{_SUFFIX}"))
            found = [j for j in jobs if j.key_id == key_id and j.created_at >= since]
            return sorted(found, key=lambda j: j.created_at)

        return await asyncio.to_thread(scan)


class _QueueEntry(MutableContract):
    message_id: str
    run_id: str
    visible_at: float
    dequeue_count: int
    receipt: str


class LocalJobQueue:
    """File-per-message queue with Azure Storage Queue semantics: receive hides a message for
    `visibility_s`, delete needs the current receipt, an undeleted message comes back."""

    def __init__(self, root: Path, clock: Clock) -> None:
        self._dir = root / "queue"
        self._clock = clock

    @contextmanager
    def _locked(self) -> Generator[None]:
        self._dir.mkdir(parents=True, exist_ok=True)
        with (self._dir / ".lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _path(self, message_id: str) -> Path:
        return self._dir / f"{check_run_id(message_id)}{_SUFFIX}"

    async def send(self, run_id: str) -> None:
        entry = _QueueEntry(
            message_id=str(ULID()), run_id=check_run_id(run_id), visible_at=self._clock(),
            dequeue_count=0, receipt="",
        )  # fmt: skip
        data = entry.model_dump_json().encode()
        await asyncio.to_thread(_atomic_write, self._path(entry.message_id), data)

    def _receive(self, visibility_s: int) -> QueueMessage | None:
        with self._locked():
            now = self._clock()
            for path in sorted(self._dir.glob(f"*{_SUFFIX}")):  # ULID names: oldest first
                entry = _QueueEntry.model_validate_json(path.read_bytes())
                if entry.visible_at > now:
                    continue
                entry.visible_at = now + visibility_s
                entry.dequeue_count += 1
                entry.receipt = str(ULID())
                _atomic_write(path, entry.model_dump_json().encode())
                return QueueMessage(
                    entry.message_id, entry.run_id, entry.receipt, entry.dequeue_count
                )
        return None

    async def receive(self, visibility_s: int) -> QueueMessage | None:
        return await asyncio.to_thread(self._receive, visibility_s)

    def _delete(self, message: QueueMessage) -> None:
        with self._locked():
            data = _read(self._path(message.message_id))
            if data is None:
                return
            if _QueueEntry.model_validate_json(data).receipt != message.receipt:
                raise StorageKeyError(f"stale receipt for message {message.message_id}")
            self._path(message.message_id).unlink()

    async def delete(self, message: QueueMessage) -> None:
        await asyncio.to_thread(self._delete, message)


def open_local_storage(data_dir: Path, *, clock: Clock = time.time) -> Storage:
    """All stores rooted at `data_dir` (created on first write)."""
    return Storage(
        artifacts=LocalArtifactStore(data_dir),
        run_state=LocalRunStateStore(data_dir),
        cache=SqliteCacheStore(data_dir / "cache.sqlite", clock=clock),
        checkpoints=LocalCheckpointStore(data_dir),
        jobs=LocalJobStore(data_dir),
        queue=LocalJobQueue(data_dir, clock),
    )
