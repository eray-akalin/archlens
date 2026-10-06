"""Ingest and profile contracts (DATA_MODEL.md §3)."""

from pydantic import AwareDatetime

from archlens.models.base import Contract


class IngestLimits(Contract):
    max_total_bytes: int = 300_000_000
    max_files: int = 30_000
    max_file_bytes: int = 2_000_000  # larger files are listed but not read
    clone_timeout_s: int = 180


class FileEntry(Contract):
    path: str
    size: int
    sha256: str  # "" when the content was not read (too_large)
    language: str | None
    loc: int
    is_binary: bool
    is_generated: bool
    is_vendored: bool
    symlink_target: str | None = None  # set for symlinks: listed with size 0, never followed
    too_large: bool = False  # size > max_file_bytes: listed, never read, scanned or indexed

    @property
    def readable(self) -> bool:
        """True if tools may read this file's content (regular, text, within the size limit)."""
        return self.symlink_target is None and not self.too_large and not self.is_binary


class RepoSnapshot(Contract):
    """File listing of one commit. The root path lives in RunContext and is never serialized."""

    repo_url: str | None
    ref: str | None
    commit_sha: str
    files: list[FileEntry]
    created_at: AwareDatetime


class RepoProfile(Contract):
    languages: dict[str, int]  # language -> LOC, descending
    frameworks: list[str]
    package_managers: list[str]
    ci_systems: list[str]
    test_frameworks: list[str]
    flags: dict[str, bool]  # catalogue in RUBRICS.md §2
    entrypoints: list[str]
