"""CodeEvidence built by the system from snapshot files (DATA_MODEL.md §2, CLAUDE.md rule 4).

Reads are jailed and redacted; snippets are normalized (trailing whitespace stripped per line,
`\\n`-joined) and hashed after redaction, so hashes are stable and never cover a secret.
"""

import hashlib
from pathlib import Path

from archlens.errors import PathOutsideSnapshot
from archlens.models import MAX_EVIDENCE_SPAN, CodeEvidence
from archlens.security.redact import Redactor
from archlens.tools.paths import resolve_in_snapshot


def normalize_snippet(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.splitlines())


def snippet_sha256(snippet: str) -> str:
    return hashlib.sha256(normalize_snippet(snippet).encode("utf-8", "replace")).hexdigest()


class SnippetReader:
    """Line-range reader over one snapshot, with a per-file cache."""

    def __init__(
        self, root: Path, redactor: Redactor | None = None, max_file_bytes: int = 2_000_000
    ):
        self.root = root
        self.redactor = redactor or Redactor()
        self.max_file_bytes = max_file_bytes
        self._lines: dict[str, list[str] | None] = {}

    def with_redactor(self, redactor: Redactor) -> "SnippetReader":
        return SnippetReader(self.root, redactor, self.max_file_bytes)

    def lines(self, path: str) -> list[str] | None:
        """Raw (unredacted) lines of `path`; None if outside the snapshot, missing or too large."""
        if path not in self._lines:
            self._lines[path] = self._load(path)
        return self._lines[path]

    def evidence(
        self, path: str, start_line: int, end_line: int | None = None
    ) -> CodeEvidence | None:
        """Evidence for lines start..end, clipped to the file and to MAX_EVIDENCE_SPAN lines."""
        lines = self.lines(path)
        if not lines:
            return None
        start = max(1, start_line)
        if start > len(lines):
            return None
        end = min(max(end_line or start, start), len(lines), start + MAX_EVIDENCE_SPAN - 1)
        excerpt = "\n".join(lines[start - 1 : end]) + "\n"
        snippet = normalize_snippet(self.redactor.redact_excerpt(path, excerpt, first_line=start))
        return CodeEvidence(
            path=path,
            start_line=start,
            end_line=end,
            snippet=snippet,
            snippet_sha256=snippet_sha256(snippet),
        )

    def _load(self, path: str) -> list[str] | None:
        try:
            resolved = resolve_in_snapshot(self.root, path)
            if not resolved.is_file() or resolved.stat().st_size > self.max_file_bytes:
                return None
            return resolved.read_bytes().decode("utf-8", "replace").splitlines()
        except (PathOutsideSnapshot, OSError):
            return None
