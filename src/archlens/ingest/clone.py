"""Hardened shallow clone (docs/SECURITY.md §3).

Every git call runs with a minimal environment built from scratch (no inherited GIT_* variables,
empty HOME, no system or global config), hooks disabled, a single allowed transport protocol, and
one shared deadline of `clone_timeout_s`. Totals are checked against `git ls-tree` *before*
checkout, so an oversized repository never reaches the disk. URL and ref are validated and the
URL is passed after `--`, so neither can be read as a git option.
"""

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from archlens.errors import IngestError
from archlens.ingest.limits import enforce_totals
from archlens.models import IngestLimits

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")


@dataclass(frozen=True)
class ClonedRepo:
    root: Path
    commit_sha: str


def hardening_config(protocol: str = "https") -> list[str]:
    """`-c` options added to every git invocation."""
    return [
        "-c", "core.hooksPath=/dev/null",
        "-c", "core.fsmonitor=false",
        "-c", "protocol.allow=never",
        "-c", f"protocol.{protocol}.allow=always",
        "-c", "submodule.recurse=false",
    ]  # fmt: skip


def git_env(home: Path) -> dict[str, str]:
    """Environment for git: nothing inherited except PATH."""
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_LFS_SKIP_SMUDGE": "1",
        "GIT_ASKPASS": shutil.which("true") or "/bin/true",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "LC_ALL": "C",
    }


def clone_command(url: str, ref: str | None, dest: Path, protocol: str = "https") -> list[str]:
    """The `git clone` argv. A full SHA is not a branch: it is fetched after the clone."""
    branch = [] if ref is None or _FULL_SHA.fullmatch(ref) else ["--branch", ref]
    return [
        "git", *hardening_config(protocol),
        "clone", "--depth", "1", "--no-tags", "--single-branch", *branch,
        "--no-recurse-submodules", "--no-checkout",
        "--", url, str(dest),
    ]  # fmt: skip


def validate_url(url: str, protocol: str = "https") -> None:
    """Raise IngestError unless `url` uses `protocol` and carries no credentials."""
    parts = urlsplit(url)
    if url.startswith("-") or parts.scheme != protocol:
        raise IngestError(f"only {protocol}:// repository URLs are allowed")
    if parts.username or parts.password:
        raise IngestError("credentials in repository URLs are not allowed")  # URL not echoed
    if protocol != "file" and not parts.hostname:
        raise IngestError("repository URL has no host")


def validate_ref(ref: str | None) -> None:
    if ref is not None and not _REF.fullmatch(ref):
        raise IngestError(f"invalid ref: {ref!r}")


def clone_repo(
    url: str,
    ref: str | None,
    workdir: Path,
    limits: IngestLimits,
    *,
    protocol: str = "https",
) -> ClonedRepo:
    """Clone `url` at `ref` (branch, tag, full commit SHA, or None for the default branch).

    The working tree lands in `workdir/repo`; `workdir` must be a fresh, caller-owned directory
    (the caller deletes it after the run). `protocol` exists for offline tests (`file`); callers
    keep the default. Raises IngestError on invalid input, git failure or timeout, and
    IngestLimitExceeded before checkout when the tree is too large.
    """
    validate_url(url, protocol)
    validate_ref(ref)
    home = workdir / "home"
    home.mkdir(parents=True)
    dest = workdir / "repo"
    env = git_env(home)
    deadline = time.monotonic() + limits.clone_timeout_s

    def git(*args: str) -> str:
        return _run(["git", "-C", str(dest), *hardening_config(protocol), *args], env, deadline)

    _run(clone_command(url, ref, dest, protocol), env, deadline)
    target = "HEAD"
    if ref is not None and _FULL_SHA.fullmatch(ref):
        if git("rev-parse", "HEAD").strip() != ref:
            git("fetch", "--depth", "1", "--no-tags", "origin", ref)
        target = ref
    total_bytes, file_count = _tree_totals(git("ls-tree", "-r", "-l", "-z", target))
    enforce_totals(total_bytes, file_count, limits, where="clone pre-flight")
    git("checkout", "--quiet", "--detach", target)
    commit_sha = git("rev-parse", "HEAD").strip()
    if target != "HEAD" and commit_sha != target:
        raise IngestError(f"checked out {commit_sha}, expected {target}")
    return ClonedRepo(root=dest, commit_sha=commit_sha)


def _run(argv: list[str], env: dict[str, str], deadline: float) -> str:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise IngestError("clone timed out")
    try:
        proc = subprocess.run(
            argv,
            env=env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=remaining,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise IngestError("clone timed out") from exc
    if proc.returncode != 0:
        command = next(
            (a for a in argv if a in {"clone", "fetch", "ls-tree", "checkout", "rev-parse"}), "git"
        )
        raise IngestError(f"git {command} failed: {proc.stderr.strip()[-500:]}")
    return proc.stdout


def _tree_totals(ls_tree_z: str) -> tuple[int, int]:
    """(total blob bytes, entry count) from `git ls-tree -r -l -z` output."""
    total = count = 0
    for record in ls_tree_z.split("\0"):
        if not record:
            continue
        meta = record.partition("\t")[0].split()
        count += 1
        if len(meta) == 4 and meta[3].isdigit():  # submodules report "-"
            total += int(meta[3])
    return total, count
