"""Hardened clone, exercised offline against a local origin over file://."""

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from archlens.errors import IngestError, IngestLimitExceeded
from archlens.ingest import clone as clone_module
from archlens.ingest.clone import (
    clone_command,
    clone_repo,
    hardening_config,
    validate_ref,
    validate_url,
)
from archlens.models import IngestLimits

LIMITS = IngestLimits()
HARDENING_OPTIONS = [
    "core.hooksPath=/dev/null",
    "core.fsmonitor=false",
    "protocol.allow=never",
    "submodule.recurse=false",
]
HARDENING_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_LFS_SKIP_SMUDGE": "1",
    "GIT_CONFIG_NOSYSTEM": "1",
}


class Origin:
    """A two-commit repository with a `release` branch at the first commit."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir()
        self._env = {
            "PATH": os.environ["PATH"],
            "HOME": str(path),  # isolate from the developer's git config (signing, hooks, ...)
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        }
        self.git("init", "-q", "-b", "main")
        self.git("config", "uploadpack.allowAnySHA1InWant", "true")
        (path / "a.txt").write_text("one\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "first")
        self.first = self.git("rev-parse", "HEAD")
        (path / "b.txt").write_text("two\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "second")
        self.second = self.git("rev-parse", "HEAD")
        self.git("branch", "release", self.first)
        self.url = f"file://{path}"

    def git(self, *args: str) -> str:
        out = subprocess.run(
            ["git", "-C", str(self.path), *args],
            env=self._env,
            check=True,
            capture_output=True,
            text=True,
        )
        return out.stdout.strip()


@pytest.fixture
def origin(tmp_path: Path) -> Origin:
    return Origin(tmp_path / "origin")


def _clone(origin: Origin, tmp_path: Path, ref: str | None = None, limits: IngestLimits = LIMITS):
    return clone_repo(origin.url, ref, tmp_path / "work", limits, protocol="file")


def test_default_branch(origin: Origin, tmp_path: Path) -> None:
    cloned = _clone(origin, tmp_path)
    assert cloned.commit_sha == origin.second
    assert (cloned.root / "b.txt").read_text() == "two\n"


def test_requested_sha_becomes_commit_sha(origin: Origin, tmp_path: Path) -> None:
    cloned = _clone(origin, tmp_path, ref=origin.first)
    assert cloned.commit_sha == origin.first
    assert (cloned.root / "a.txt").exists() and not (cloned.root / "b.txt").exists()


def test_branch_ref(origin: Origin, tmp_path: Path) -> None:
    assert _clone(origin, tmp_path, ref="release").commit_sha == origin.first


def test_unknown_sha_fails(origin: Origin, tmp_path: Path) -> None:
    with pytest.raises(IngestError, match="git fetch failed"):
        _clone(origin, tmp_path, ref="f" * 40)


def test_limits_are_enforced_before_checkout(origin: Origin, tmp_path: Path) -> None:
    with pytest.raises(IngestLimitExceeded, match="clone pre-flight"):
        _clone(origin, tmp_path, limits=IngestLimits(max_files=1))
    repo = tmp_path / "work" / "repo"
    assert sorted(p.name for p in repo.iterdir()) == [".git"]  # nothing checked out


def test_timeout(origin: Origin, tmp_path: Path) -> None:
    with pytest.raises(IngestError, match="timed out"):
        _clone(origin, tmp_path, limits=IngestLimits(clone_timeout_s=0))


def test_every_git_call_is_hardened(
    origin: Origin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []
    real_run = subprocess.run

    def recording_run(argv: list[str], **kwargs: Any) -> Any:
        calls.append((argv, kwargs["env"]))
        return real_run(argv, **kwargs)

    monkeypatch.setattr(clone_module.subprocess, "run", recording_run)
    _clone(origin, tmp_path, ref=origin.first)

    subcommands = [
        next(a for a in argv if a in {"clone", "fetch", "ls-tree", "checkout", "rev-parse"})
        for argv, _ in calls
    ]
    assert subcommands == ["clone", "rev-parse", "fetch", "ls-tree", "checkout", "rev-parse"]
    for argv, env in calls:
        for option in [*HARDENING_OPTIONS, "protocol.file.allow=always"]:
            assert option in argv, (option, argv)
        assert HARDENING_ENV.items() <= env.items()
        assert env["HOME"] == str(tmp_path / "work" / "home")
        assert not any(k.startswith("GIT_DIR") or k == "GIT_WORK_TREE" for k in env)


def test_clone_command_for_https() -> None:
    argv = clone_command("https://github.com/org/repo", "main", Path("/tmp/x"))
    for flag in [
        "--depth",
        "--no-tags",
        "--single-branch",
        "--no-recurse-submodules",
        "--no-checkout",
    ]:
        assert flag in argv
    assert argv[argv.index("--depth") + 1] == "1"
    assert argv[argv.index("--branch") + 1] == "main"
    assert argv[-3:] == ["--", "https://github.com/org/repo", "/tmp/x"]
    assert "protocol.https.allow=always" in argv and "protocol.allow=never" in argv
    assert hardening_config()[1] == "core.hooksPath=/dev/null"


def test_full_sha_is_not_passed_as_branch() -> None:
    assert "--branch" not in clone_command("https://github.com/o/r", "a" * 40, Path("/tmp/x"))


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://github.com/o/r", "only https://"),
        ("file:///etc", "only https://"),
        ("-uhttps://github.com/o/r", "only https://"),
        ("https://user:token@github.com/o/r", "credentials"),
        ("https:///o/r", "no host"),
    ],
)
def test_validate_url(url: str, message: str) -> None:
    with pytest.raises(IngestError, match=message) as info:
        validate_url(url)
    assert "token" not in str(info.value)


@pytest.mark.parametrize("ref", ["-x", "--upload-pack=touch /tmp/pwned", "a b", "../x", ""])
def test_validate_ref_rejects_option_like_and_odd_refs(ref: str) -> None:
    with pytest.raises(IngestError, match="invalid ref"):
        validate_ref(ref)


@pytest.mark.parametrize("ref", [None, "main", "release/1.2", "v1.0.0", "a" * 40])
def test_validate_ref_accepts_normal_refs(ref: str | None) -> None:
    validate_ref(ref)
