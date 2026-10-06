"""`RuleContext`: everything a deterministic rule may look at (DATA_MODEL.md §5).

Rules get facts, the profile and the full file listing. Their only file access is `read_text` /
`evidence`, which read listed, readable snapshot files through the jail (`SnippetReader`) and
redact secrets before returning anything, so evidence hashes match what the verifier computes.
"""

from dataclasses import dataclass, field
from pathlib import Path

from archlens.evidence import SnippetReader
from archlens.models import CodeEvidence, FactSet, FileEntry, RepoProfile, ToolRunRecord
from archlens.security.redact import Redactor

DEFAULT_READ_BYTES = 200_000


@dataclass(frozen=True)
class RuleContext:
    facts: FactSet
    profile: RepoProfile
    files: list[FileEntry]  # full snapshot listing (for glob-based rules)
    reader: SnippetReader = field(repr=False)
    _entries: dict[str, FileEntry] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_entries", {f.path: f for f in self.files})

    @classmethod
    def create(
        cls,
        root: Path,
        facts: FactSet,
        profile: RepoProfile,
        files: list[FileEntry],
        redactor: Redactor | None = None,
    ) -> "RuleContext":
        """Context over the snapshot at `root`; pass the facts stage's redactor (gitleaks spans)."""
        return cls(facts, profile, files, SnippetReader(root, redactor))

    def read_text(self, path: str, max_bytes: int = DEFAULT_READ_BYTES) -> str | None:
        """Jailed, redacted, size-capped read for config files (coverage config, compose, k8s).

        None if `path` is not a readable file of the listing (symlinks, binaries and oversized
        files are never read), escapes the snapshot, or is larger than `max_bytes`.
        """
        entry = self._entries.get(path)
        if entry is None or not entry.readable or entry.size > max_bytes:
            return None
        lines = self.reader.lines(path)
        if lines is None:
            return None
        return self.reader.redactor.redact_excerpt(path, "\n".join(lines) + "\n")

    def evidence(
        self, path: str, start_line: int, end_line: int | None = None
    ) -> CodeEvidence | None:
        """Redacted, hashed CodeEvidence for lines of a listed file; None if it can't be read."""
        entry = self._entries.get(path)
        if entry is None or not entry.readable:
            return None
        return self.reader.evidence(path, start_line, end_line)

    def tool_run(self, tool: str) -> ToolRunRecord | None:
        """Run record of a scanner (`gitleaks`) or extractor (`ast:ci`); None if it never ran."""
        return next((r for r in reversed(self.facts.tool_runs) if r.tool == tool), None)
