import hashlib
from pathlib import Path

import pytest

from archlens.errors import IngestError, IngestLimitExceeded
from archlens.ingest import ingest
from archlens.ingest.snapshot import build_snapshot
from archlens.models import IngestLimits
from tests.fixture_repos import MaterializedRepo

LIMITS = IngestLimits()


def _write(root: Path, rel: str, data: bytes | str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode()
    path.write_bytes(data)
    return path


def test_tiny_service_snapshot(tiny_service: MaterializedRepo) -> None:
    snap = build_snapshot(tiny_service.root, limits=LIMITS)
    paths = [f.path for f in snap.files]
    assert paths == sorted(paths)
    assert "app/api/users.py" in paths and ".github/workflows/ci.yml" in paths
    assert "DEFECTS.md" not in paths
    users = next(f for f in snap.files if f.path == "app/api/users.py")
    assert users.language == "python" and users.loc > 40 and users.readable
    assert users.sha256 == hashlib.sha256((tiny_service.root / users.path).read_bytes()).hexdigest()
    assert snap.repo_url is None and len(snap.commit_sha) == 40


def test_git_directory_is_skipped(tmp_path: Path) -> None:
    _write(tmp_path, ".git/config", "[core]\n\tfsmonitor = touch /tmp/pwned\n")
    _write(tmp_path, "a.py", "x = 1\n")
    assert [f.path for f in build_snapshot(tmp_path, limits=LIMITS).files] == ["a.py"]


def test_binary_vendored_generated_flags(tmp_path: Path) -> None:
    _write(tmp_path, "logo.png", b"\x89PNG\0\0\0")
    _write(tmp_path, "node_modules/x/index.js", "module.exports = 1\n")
    _write(tmp_path, "dist/app.min.js", "var a=1;")
    files = {f.path: f for f in build_snapshot(tmp_path, limits=LIMITS).files}
    assert files["logo.png"].is_binary and files["logo.png"].loc == 0
    assert not files["logo.png"].readable
    assert files["node_modules/x/index.js"].is_vendored
    assert files["dist/app.min.js"].is_generated


def test_oversized_file_is_listed_but_never_read(tmp_path: Path) -> None:
    big = _write(tmp_path, "data/big.sql", "x" * 100)
    big.chmod(0)  # reading it would raise PermissionError
    try:
        snap = build_snapshot(tmp_path, limits=IngestLimits(max_file_bytes=10))
    finally:
        big.chmod(0o644)
    entry = snap.files[0]
    assert (entry.path, entry.size, entry.too_large) == ("data/big.sql", 100, True)
    assert entry.sha256 == "" and entry.loc == 0 and not entry.readable


def test_symlinks_are_listed_with_target_and_never_followed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    outside = _write(tmp_path, "secret.txt", "TOP SECRET\n")
    outside_dir = tmp_path / "elsewhere"
    _write(outside_dir, "inner.py", "print('outside')\n")
    _write(root, "a.py", "x = 1\n")
    (root / "leak.txt").symlink_to(outside)
    (root / "linked_dir").symlink_to(outside_dir, target_is_directory=True)
    (root / "dangling").symlink_to(root / "missing")

    files = {f.path: f for f in build_snapshot(root, limits=LIMITS).files}
    assert set(files) == {"a.py", "dangling", "leak.txt", "linked_dir"}  # inner.py not walked
    leak = files["leak.txt"]
    assert leak.symlink_target == str(outside)
    assert leak.size == 0 and leak.loc == 0 and not leak.readable
    assert leak.sha256 == hashlib.sha256(str(outside).encode()).hexdigest()


@pytest.mark.parametrize(
    ("limits", "message"),
    [
        (IngestLimits(max_files=2), "max_files=2"),
        (IngestLimits(max_total_bytes=10), "max_total_bytes"),
    ],
)
def test_limits_fail_ingest(tmp_path: Path, limits: IngestLimits, message: str) -> None:
    for name in ("a.py", "b.py", "c.py"):
        _write(tmp_path, name, "x = 1\n")
    with pytest.raises(IngestLimitExceeded, match=message):
        build_snapshot(tmp_path, limits=limits)


def test_content_id_is_deterministic_and_content_sensitive(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "x = 1\n")
    first = build_snapshot(tmp_path, limits=LIMITS).commit_sha
    assert build_snapshot(tmp_path, limits=LIMITS).commit_sha == first
    _write(tmp_path, "a.py", "x = 2\n")
    assert build_snapshot(tmp_path, limits=LIMITS).commit_sha != first


def test_local_snapshot_does_not_modify_the_directory(tiny_service: MaterializedRepo) -> None:
    def state() -> list[tuple[str, int, int]]:
        return sorted(
            (str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in tiny_service.root.rglob("*")
        )

    before = state()
    build_snapshot(tiny_service.root, limits=LIMITS)
    assert state() == before


def test_not_a_directory(tmp_path: Path) -> None:
    with pytest.raises(IngestError, match="not a directory"):
        build_snapshot(tmp_path / "nope", limits=LIMITS)


def test_ingest_local_path(tiny_service: MaterializedRepo, tmp_path: Path) -> None:
    result = ingest(str(tiny_service.root), workdir=tmp_path / "work")
    assert result.root == tiny_service.root.resolve()
    assert any(f.path == "Dockerfile" for f in result.snapshot.files)
    assert not (tmp_path / "work").exists()  # local paths never use the workdir


def test_ingest_rejects_ref_for_local_paths(tmp_path: Path) -> None:
    with pytest.raises(IngestError, match="remote"):
        ingest(str(tmp_path), workdir=tmp_path / "work", ref="main")


@pytest.mark.parametrize(
    "url", ["http://github.com/a/b", "ssh://git@github.com/a/b", "git@github.com:a/b"]
)
def test_ingest_rejects_non_https_remotes(tmp_path: Path, url: str) -> None:
    with pytest.raises(IngestError, match="only https://"):
        ingest(url, workdir=tmp_path / "work")


def test_symlink_root_is_resolved(tiny_service: MaterializedRepo, tmp_path: Path) -> None:
    link = tmp_path / "link_to_repo"
    link.symlink_to(tiny_service.root, target_is_directory=True)
    via_link = build_snapshot(link, limits=LIMITS)
    direct = build_snapshot(tiny_service.root, limits=LIMITS)
    assert [f.path for f in via_link.files] == [f.path for f in direct.files]
