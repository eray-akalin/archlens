"""Shared building blocks for mutations: file walking, line edits, unified-diff patches.

Edits are plain text operations on a temp copy of the base repo; nothing in the repo is executed.
Patches are applied with `git apply` (a text operation, outside any repository, no hooks).
"""

import base64
import os
import random
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import yaml

from archlens.errors import ArchLensError
from archlens.eval.mutation import ChangedLines, MutationResult
from archlens.facts.extractors.common import is_test_path
from archlens.ingest.filters import is_vendored

PATCHES_DIR = Path("eval/patches")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class MutationError(ArchLensError):
    """A mutation could not be applied to this repository."""


def files(repo: Path) -> Iterator[str]:
    """Repo-relative POSIX paths of regular files, sorted, without .git and vendored trees."""
    for path in sorted(repo.rglob("*")):
        rel = path.relative_to(repo).as_posix()
        if (
            path.is_file()
            and not path.is_symlink()
            and not rel.startswith(".git/")
            and not is_vendored(rel)
        ):
            yield rel


def read_lines(repo: Path, rel: str) -> list[str]:
    return (repo / rel).read_text(encoding="utf-8").splitlines(keepends=True)


def write_lines(repo: Path, rel: str, lines: list[str]) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(lines), encoding="utf-8")


def insert(repo: Path, rel: str, index: int, new: list[str]) -> ChangedLines:
    """Insert `new` lines before 0-based `index`; returns their 1-based range."""
    lines = read_lines(repo, rel) if (repo / rel).exists() else []
    lines[index:index] = [line if line.endswith("\n") else line + "\n" for line in new]
    write_lines(repo, rel, lines)
    return ChangedLines(rel, index + 1, index + len(new))


def replace(repo: Path, rel: str, start: int, end: int, new: list[str]) -> ChangedLines:
    """Replace 0-based lines [start, end) with `new`; returns the new lines' 1-based range (or the
    line after the cut when `new` is empty)."""
    lines = read_lines(repo, rel)
    lines[start:end] = [line if line.endswith("\n") else line + "\n" for line in new]
    write_lines(repo, rel, lines)
    if new:
        return ChangedLines(rel, start + 1, start + len(new))
    line = min(start + 1, max(len(lines), 1))
    return ChangedLines(rel, line, line)


def first_index(lines: list[str], pattern: str) -> int | None:
    regex = re.compile(pattern)
    return next((i for i, line in enumerate(lines) if regex.search(line)), None)


def test_files(repo: Path) -> list[str]:
    return [p for p in files(repo) if is_test_path(p)]


def workflows(repo: Path) -> list[str]:
    return [
        p
        for p in files(repo)
        if p.startswith(".github/workflows/") and p.endswith((".yml", ".yaml"))
    ]


def dockerfiles(repo: Path) -> list[str]:
    names = re.compile(r"(^|/)(Dockerfile[^/]*|[^/]*\.Dockerfile|Containerfile)$")
    return [p for p in files(repo) if names.search(p)]


def yaml_root(repo: Path, rel: str) -> yaml.MappingNode | None:
    try:
        text = (repo / rel).read_text(encoding="utf-8")
        node = cast(yaml.Node | None, yaml.compose(text, Loader=yaml.SafeLoader))  # pyright: ignore[reportUnknownMemberType]
    except yaml.YAMLError:
        return None
    return node if isinstance(node, yaml.MappingNode) else None


def child(node: yaml.Node | None, key: str) -> tuple[yaml.ScalarNode, yaml.Node] | None:
    if isinstance(node, yaml.MappingNode):
        for k, v in node.value:
            if isinstance(k, yaml.ScalarNode) and k.value == key:
                return k, v
    return None


def generated_private_key(rng: random.Random) -> list[str]:
    """A private-key-shaped PEM block with random content, made at mutation time inside the temp
    copy and never committed."""
    body = base64.b64encode(rng.randbytes(1190)).decode()
    return [
        "-----BEGIN RSA PRIVATE KEY-----",
        *(body[i : i + 64] for i in range(0, len(body), 64)),
        "-----END RSA PRIVATE KEY-----",
    ]


def apply_patch(repo: Path, repo_id: str, mutation_id: str) -> MutationResult:
    """Apply `eval/patches/<repo_id>/<mutation_id>.patch`; changed lines are the added lines (a
    pure deletion reports the line where it happened). Raises MutationError."""
    patch = PATCHES_DIR / repo_id / f"{mutation_id}.patch"
    if not patch.is_file():
        raise MutationError(f"{mutation_id}: no patch for {repo_id} at {patch}")
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(repo),
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    done = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", str(patch.resolve())],
        cwd=repo, env=env, capture_output=True, text=True, check=False,
    )  # fmt: skip
    if done.returncode != 0:
        raise MutationError(f"{mutation_id}: patch does not apply: {done.stderr.strip()[:300]}")
    return MutationResult(tuple(patch_changes(patch.read_text(encoding="utf-8"))))


def patch_changes(text: str) -> list[ChangedLines]:
    """Added-line ranges (new-file numbering) of a unified diff."""
    added: list[ChangedLines] = []
    cuts: list[ChangedLines] = []  # pure deletions: the line where they happened
    path = ""
    line = 0
    run_start: int | None = None
    for raw in text.splitlines():
        if raw.startswith("+++ "):
            path = raw[4:].strip().removeprefix("b/")
            continue
        if raw.startswith("--- "):
            continue
        if hunk := _HUNK.match(raw):
            line = int(hunk.group(1))
            continue
        if raw.startswith("+"):
            run_start = line if run_start is None else run_start
            line += 1
            continue
        if run_start is not None:
            added.append(ChangedLines(path, run_start, line - 1))
            run_start = None
        if raw.startswith("-"):
            cuts.append(ChangedLines(path, max(line, 1), max(line, 1)))
            continue
        line += 1
    if run_start is not None:
        added.append(ChangedLines(path, run_start, line - 1))
    kept = [
        c for i, c in enumerate(cuts)
        if c not in cuts[:i]
        and not any(a.path == c.path and a.start_line <= c.start_line <= a.end_line for a in added)
    ]  # fmt: skip
    return sorted([*added, *kept], key=lambda c: (c.path, c.start_line))
