"""Build a RepoSnapshot from a directory (a fresh clone or a user's local checkout).

Read-only: the directory is never modified, symlinks are never followed, `.git/` is skipped, and
files above `max_file_bytes` are listed without being read. For local paths the snapshot ID is a
digest of the file contents — git is never run inside a directory we don't control, because a
repository's own `.git/config` can make some git commands execute programs.
"""

import hashlib
import os
import stat
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from archlens.errors import IngestError
from archlens.ingest.filters import count_loc, detect_language, is_binary, is_generated, is_vendored
from archlens.ingest.limits import enforce_totals
from archlens.models import FileEntry, IngestLimits, RepoSnapshot

_SKIP_DIRS = frozenset({".git"})


def build_snapshot(
    root: Path,
    *,
    limits: IngestLimits,
    repo_url: str | None = None,
    ref: str | None = None,
    commit_sha: str | None = None,
) -> RepoSnapshot:
    """Walk `root` and describe every file.

    `commit_sha=None` (local paths) derives a 40-hex content ID from the sorted (path, sha256)
    list, so unchanged content always gets the same ID. Raises IngestError if `root` is not a
    directory and IngestLimitExceeded if the totals exceed `limits`.
    """
    root = root.resolve()
    if not root.is_dir():
        raise IngestError(f"not a directory: {root}")

    stats = list(_walk(root))
    enforce_totals(sum(st.st_size for _, _, st in stats), len(stats), limits, where="snapshot")
    files = sorted(
        (_entry(abs_path, rel, st, limits) for abs_path, rel, st in stats), key=lambda f: f.path
    )
    return RepoSnapshot(
        repo_url=repo_url,
        ref=ref,
        commit_sha=commit_sha or content_id(files),
        files=files,
        created_at=datetime.now(UTC),
    )


def content_id(files: list[FileEntry]) -> str:
    """Deterministic 40-hex ID of a file listing (path and content hash of every entry)."""
    digest = hashlib.sha256()
    for f in sorted(files, key=lambda e: e.path):
        digest.update(f"{f.path}\0{f.sha256}\0{f.symlink_target or ''}\n".encode())
    return digest.hexdigest()[:40]


def _walk(root: Path) -> Iterator[tuple[Path, str, os.stat_result]]:
    """(absolute path, repo-relative POSIX path, lstat) for every regular file and symlink."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        # Symlinked directories are not descended into by os.walk; list them as entries.
        names = filenames + [d for d in dirnames if (here / d).is_symlink()]
        for name in sorted(names):
            abs_path = here / name
            st = abs_path.lstat()
            if stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
                yield abs_path, abs_path.relative_to(root).as_posix(), st


def _entry(abs_path: Path, rel: str, st: os.stat_result, limits: IngestLimits) -> FileEntry:
    language, vendored = detect_language(rel), is_vendored(rel)
    if stat.S_ISLNK(st.st_mode):
        target = str(abs_path.readlink())  # the link itself, never what it points to
        return FileEntry(
            path=rel,
            size=0,
            sha256=hashlib.sha256(target.encode()).hexdigest(),
            language=language,
            loc=0,
            is_binary=False,
            is_generated=False,
            is_vendored=vendored,
            symlink_target=target,
        )
    if st.st_size > limits.max_file_bytes:
        return FileEntry(
            path=rel,
            size=st.st_size,
            sha256="",
            language=language,
            loc=0,
            is_binary=False,
            is_generated=is_generated(rel, b""),
            is_vendored=vendored,
            too_large=True,
        )
    data = abs_path.read_bytes()
    binary = is_binary(data)
    return FileEntry(
        path=rel,
        size=st.st_size,
        sha256=hashlib.sha256(data).hexdigest(),
        language=language,
        loc=0 if binary else count_loc(data),
        is_binary=binary,
        is_generated=is_generated(rel, data),
        is_vendored=vendored,
    )
