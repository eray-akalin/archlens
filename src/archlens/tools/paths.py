"""Snapshot jail (docs/SECURITY.md §4): every repo-relative path a tool, probe or evidence reader
touches goes through `resolve_in_snapshot`.
"""

import re
from pathlib import Path, PurePosixPath

from archlens.errors import PathOutsideSnapshot

_DRIVE = re.compile(r"^[A-Za-z]:")


def resolve_in_snapshot(root: Path, user_path: str) -> Path:
    """Resolve `user_path` (repo-relative) to an absolute path inside `root`.

    Rejects absolute paths, NUL bytes and drive letters; follows symlinks (including chains) and
    rejects any result outside `root`. The comparison is by path components, so a sibling such as
    `<root>-evil` is outside. `""` and `"."` resolve to `root`. The result may not exist.
    Raises PathOutsideSnapshot.
    """
    if "\0" in user_path:
        raise PathOutsideSnapshot("path contains a NUL byte")
    if (
        _DRIVE.match(user_path)
        or PurePosixPath(user_path).is_absolute()
        or user_path.startswith("\\")
    ):
        raise PathOutsideSnapshot(f"absolute paths are not allowed: {user_path!r}")
    real_root = root.resolve()
    resolved = (real_root / user_path).resolve(strict=False)
    if not resolved.is_relative_to(real_root):
        raise PathOutsideSnapshot(f"path escapes the snapshot: {user_path!r}")
    return resolved


def to_repo_path(root: Path, path: Path) -> str:
    """POSIX repo-relative form of an absolute path returned by `resolve_in_snapshot`."""
    rel = path.relative_to(root.resolve()).as_posix()
    return "" if rel == "." else rel
