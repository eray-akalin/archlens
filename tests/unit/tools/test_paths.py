"""Snapshot jail — every case in docs/SECURITY.md §4."""

from pathlib import Path

import pytest

from archlens.errors import PathOutsideSnapshot
from archlens.tools.paths import resolve_in_snapshot, to_repo_path


@pytest.fixture
def root(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "main.py").write_text("x = 1\n")
    (tmp_path / "outside.txt").write_text("secret\n")
    (tmp_path / "outside_dir").mkdir()
    (tmp_path / "outside_dir" / "inner.txt").write_text("secret\n")
    return repo


@pytest.mark.parametrize(
    ("user_path", "expected"),
    [
        ("app/main.py", "app/main.py"),
        ("", ""),
        (".", ""),
        ("app/../app/main.py", "app/main.py"),
        ("new.txt", "new.txt"),
    ],
)
def test_paths_inside_resolve(root: Path, user_path: str, expected: str) -> None:
    resolved = resolve_in_snapshot(root, user_path)
    assert to_repo_path(root, resolved) == expected


@pytest.mark.parametrize(
    "user_path", ["..", "../outside.txt", "app/../../outside.txt", "app/../../repo-evil/x"]
)
def test_dotdot_escapes_are_rejected(root: Path, user_path: str) -> None:
    with pytest.raises(PathOutsideSnapshot, match="escapes"):
        resolve_in_snapshot(root, user_path)


@pytest.mark.parametrize(
    "user_path", ["/etc/passwd", "//server/share/x", "\\\\server\\share", "C:\\Windows", "c:x"]
)
def test_absolute_and_drive_paths_are_rejected(root: Path, user_path: str) -> None:
    with pytest.raises(PathOutsideSnapshot, match="absolute"):
        resolve_in_snapshot(root, user_path)


def test_nul_bytes_are_rejected(root: Path) -> None:
    with pytest.raises(PathOutsideSnapshot, match="NUL"):
        resolve_in_snapshot(root, "app/main.py\0.txt")


def test_symlink_to_outside_is_rejected(root: Path, tmp_path: Path) -> None:
    (root / "leak").symlink_to(tmp_path / "outside.txt")
    with pytest.raises(PathOutsideSnapshot):
        resolve_in_snapshot(root, "leak")


def test_symlinked_directory_to_outside_is_rejected(root: Path, tmp_path: Path) -> None:
    (root / "linked").symlink_to(tmp_path / "outside_dir", target_is_directory=True)
    with pytest.raises(PathOutsideSnapshot):
        resolve_in_snapshot(root, "linked/inner.txt")


def test_symlink_chain_ending_outside_is_rejected(root: Path, tmp_path: Path) -> None:
    (root / "c").symlink_to(tmp_path / "outside.txt")
    (root / "b").symlink_to(root / "c")
    (root / "a").symlink_to("b")  # relative link
    with pytest.raises(PathOutsideSnapshot):
        resolve_in_snapshot(root, "a")


def test_symlink_chain_inside_resolves_to_final_target(root: Path) -> None:
    (root / "b").symlink_to("app/main.py")
    (root / "a").symlink_to("b")
    assert to_repo_path(root, resolve_in_snapshot(root, "a")) == "app/main.py"


def test_sibling_with_root_name_prefix_is_outside(root: Path, tmp_path: Path) -> None:
    evil = tmp_path / "repo-evil"
    evil.mkdir()
    (root / "x").symlink_to(evil)
    with pytest.raises(PathOutsideSnapshot):
        resolve_in_snapshot(root, "x")


def test_case_variant_of_a_link_never_escapes(root: Path, tmp_path: Path) -> None:
    """On case-insensitive filesystems `link` opens `Link`; the jail must still see the symlink."""
    (root / "Link").symlink_to(tmp_path / "outside_dir", target_is_directory=True)
    try:
        resolved = resolve_in_snapshot(root, "link/inner.txt")
    except PathOutsideSnapshot:
        return  # case-insensitive filesystem: the symlink was followed and rejected
    assert resolved.is_relative_to(root.resolve())  # case-sensitive: a different, missing path
    assert not resolved.exists()


def test_case_variant_of_root_is_not_inside(root: Path) -> None:
    with pytest.raises(PathOutsideSnapshot):
        resolve_in_snapshot(root, "../REPO/app/main.py")


def test_symlinked_root_is_resolved(root: Path, tmp_path: Path) -> None:
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    assert resolve_in_snapshot(alias, "app/main.py") == (root / "app" / "main.py").resolve()
