"""Shared machinery for scanner adapters: context, fact IDs, subprocess runner.

Adapters never execute repository code. Each runs with an environment built from scratch (PATH,
locale and a throwaway HOME only — none of our ARCHLENS_* or cloud credentials), from a scratch
working directory outside the snapshot so no repo-level tool config is discovered from cwd. A
missing binary, non-zero exit, timeout or unparseable output becomes a ToolRunRecord with status
error/timeout; it never raises.
"""

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path
from typing import ClassVar

from pydantic import JsonValue

from archlens.config import ToolConfig, ToolsConfig
from archlens.evidence import SnippetReader
from archlens.models import (
    CodeEvidence,
    Evidence,
    Fact,
    FileEntry,
    RepoSnapshot,
    ScanEvidence,
    Severity,
    ToolRunRecord,
    ToolStatus,
)
from archlens.security.redact import Redactor, redact

logger = logging.getLogger(__name__)

_VERSION = re.compile(r"\d+\.\d+(?:\.\d+)?")


@dataclass(frozen=True)
class ScanContext:
    root: Path  # snapshot root (read-only)
    snapshot: RepoSnapshot
    workdir: Path  # scratch space outside the snapshot: generated configs, HOME
    tools: ToolsConfig
    tools_dir: Path  # pinned rule packs (e.g. semgrep) installed by scripts/install_tools.sh
    reader: SnippetReader

    @classmethod
    def create(
        cls,
        root: Path,
        snapshot: RepoSnapshot,
        workdir: Path,
        tools: ToolsConfig,
        tools_dir: Path,
        max_file_bytes: int = 2_000_000,
    ) -> "ScanContext":
        """Context with a fresh SnippetReader; `workdir` is created if missing."""
        workdir.mkdir(parents=True, exist_ok=True)
        reader = SnippetReader(root, max_file_bytes=max_file_bytes)
        return cls(root, snapshot, workdir, tools, tools_dir, reader)

    def with_redactor(self, redactor: Redactor) -> "ScanContext":
        return replace(self, reader=self.reader.with_redactor(redactor))

    def tool(self, name: str) -> ToolConfig:
        return self.tools.tools.get(name) or ToolConfig(version="unpinned", timeout_s=120)

    def rel(self, reported: str) -> str:
        """Repo-relative POSIX path for a path a tool reported (absolute or relative to root)."""
        path = Path(reported)
        if path.is_absolute():
            try:
                return path.resolve().relative_to(self.root.resolve()).as_posix()
            except ValueError:
                return path.as_posix()
        return Path(reported.lstrip("/")).as_posix()


def make_fact(
    kind: str,
    source: str,
    attributes: dict[str, JsonValue],
    evidence: Sequence[Evidence],
    severity: Severity | None = None,
) -> Fact:
    """Fact with the deterministic ID `kind:sha1(source, kind, attributes, evidence locs)[:12]`."""
    locs = [
        f"{e.path}:{e.start_line}-{e.end_line}"
        if isinstance(e, CodeEvidence)
        else f"{e.tool}:{e.query}"
        for e in evidence
    ]
    payload = json.dumps([source, kind, attributes, locs], sort_keys=True, default=str)
    return Fact(
        id=f"{kind}:{hashlib.sha1(payload.encode()).hexdigest()[:12]}",
        kind=kind,
        source=source,
        severity=severity,
        attributes=attributes,
        evidence=list(evidence),
    )


def evidence_or_scan(
    ctx: ScanContext, path: str, start: int, end: int | None, *, tool: str, version: str, query: str
) -> list[Evidence]:
    """Code evidence for the location, or a ScanEvidence record when the file can't be read."""
    code = ctx.reader.evidence(path, start, end)
    if code is not None:
        return [code]
    return [
        ScanEvidence(tool=tool, tool_version=version, query=f"{query} @ {path}", result_count=1)
    ]


def tool_env(workdir: Path) -> dict[str, str]:
    home = workdir / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "SEMGREP_SEND_METRICS": "off",
        "SEMGREP_ENABLE_VERSION_CHECK": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for name in ("LANG", "LC_ALL"):
        if name in os.environ:
            env[name] = os.environ[name]
    return env


@cache
def installed_version(binary: str, *args: str) -> str | None:
    """First version-looking token of `<binary> <args>`; None if it can't be determined."""
    try:
        out = subprocess.run(
            [binary, *args], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = _VERSION.search(out.stdout + out.stderr)
    return match.group(0) if match else None


class SubprocessAdapter(ABC):
    """Template for adapters that shell out to a scanner binary."""

    tool: ClassVar[str]
    binary: ClassVar[str]
    version_args: ClassVar[tuple[str, ...]] = ("--version",)
    ok_exit_codes: ClassVar[frozenset[int]] = frozenset({0})

    @abstractmethod
    def targets(self, files: list[FileEntry]) -> list[str]:
        """Repo-relative paths this tool should look at; empty → status `skipped`."""

    @abstractmethod
    def options(self, ctx: ScanContext) -> list[str]:
        """argv after the binary, without targets."""

    @abstractmethod
    def parse(self, stdout: str, ctx: ScanContext, version: str) -> list[Fact]: ...

    def target_args(self, ctx: ScanContext, targets: list[str]) -> list[str]:
        """Default: every target as an absolute path (bypasses repo-level ignore files)."""
        return [str(ctx.root / t) for t in targets]

    def cwd(self, ctx: ScanContext) -> Path:
        """Working directory for the tool; default is the scratch dir (no repo config discovery)."""
        return ctx.workdir

    def run(self, ctx: ScanContext) -> tuple[list[Fact], ToolRunRecord]:
        config = ctx.tool(self.tool)
        targets = self.targets(ctx.snapshot.files)
        exe = shutil.which(self.binary)
        version = (installed_version(exe, *self.version_args) if exe else None) or config.version
        if version != config.version and exe:
            logger.warning("%s %s installed, %s pinned", self.tool, version, config.version)
        options = self.options(ctx) if targets else []
        summary = [self.binary, *self._placeholders(options, ctx), f"<{len(targets)} targets>"]

        def record(
            status: ToolStatus, started: float, facts: int = 0, error: str | None = None
        ) -> ToolRunRecord:
            return ToolRunRecord(
                tool=self.tool,
                version=version,
                command=summary,
                status=status,
                duration_ms=int((time.monotonic() - started) * 1000),
                fact_count=facts,
                error=redact(error)[-500:] if error else None,
            )

        started = time.monotonic()
        if not targets:
            return [], record("skipped", started)
        if exe is None:
            return [], record("error", started, error=f"{self.binary} not found on PATH")
        try:
            proc = subprocess.run(
                [exe, *options, *self.target_args(ctx, targets)],
                cwd=self.cwd(ctx),
                env=tool_env(ctx.workdir),
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=config.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return [], record("timeout", started, error=f"timed out after {config.timeout_s}s")
        except OSError as exc:
            return [], record("error", started, error=str(exc))
        if proc.returncode not in self.ok_exit_codes:
            return [], record(
                "error", started, error=f"exit {proc.returncode}: {proc.stderr.strip()}"
            )
        try:
            facts = self.parse(proc.stdout, ctx, version)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            return [], record("error", started, error=f"unparseable output: {exc}")
        return facts, record("ok", started, facts=len(facts))

    @staticmethod
    def _placeholders(argv: list[str], ctx: ScanContext) -> list[str]:
        swaps = [
            (str(ctx.root), "<repo>"),
            (str(ctx.workdir), "<work>"),
            (str(ctx.tools_dir), "<tools>"),
        ]
        out: list[str] = []
        for arg in argv:
            for real, placeholder in swaps:
                arg = arg.replace(real, placeholder)
            out.append(arg)
        return out
