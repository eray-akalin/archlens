"""Step 1 — mechanical citation check (ARCHITECTURE.md §2.5, CLAUDE.md rule 4).

Turns each `Citation` into `CodeEvidence` read, redacted and hashed from the snapshot. A citation
passes only if its path is a listed, readable file inside the snapshot, `1 ≤ start ≤ end ≤ file
length`, the span is under 60 lines, and every cited line is in the seen-lines ledger of the
session that produced it. Evidence a result already carries (e.g. from a checkpoint) must still
hash to the snapshot's text. One failing citation fails the step: the model's evidence is taken
as a whole or not at all.
"""

import posixpath
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from archlens.errors import PathOutsideSnapshot
from archlens.evidence import SnippetReader
from archlens.models import (
    MAX_EVIDENCE_SPAN,
    Citation,
    CodeEvidence,
    Evidence,
    FileEntry,
    VerificationStep,
)
from archlens.tools.paths import resolve_in_snapshot
from archlens.tools.seen import SeenLines


@dataclass(frozen=True)
class MechanicalResult:
    step: VerificationStep
    evidence: list[CodeEvidence]  # one per citation when the step passed

    @property
    def passed(self) -> bool:
        return self.step.passed


def check_citations(
    citations: Sequence[Citation],
    *,
    session_id: str | None,
    reader: SnippetReader,
    ledger: SeenLines,
    listing: Mapping[str, FileEntry],
    existing: Sequence[Evidence] = (),
) -> MechanicalResult:
    """Validate `citations` for `session_id` and build their evidence. Never raises."""
    problems: list[str] = []
    evidence: list[CodeEvidence] = []
    if not citations:
        problems.append("no citations")
    if session_id is None:
        problems.append("result has no session to check cited lines against")
    for citation in citations:
        built = _citation(citation, session_id, reader, ledger, listing)
        if isinstance(built, str):
            problems.append(built)
        else:
            evidence.append(built)
    for item in existing:
        if isinstance(item, CodeEvidence):
            fresh = reader.evidence(item.path, item.start_line, item.end_line)
            if fresh is None or fresh.snippet_sha256 != item.snippet_sha256:
                problems.append(
                    f"{item.path}:{item.start_line}-{item.end_line}: snippet hash mismatch"
                )
    if problems:
        shown = "; ".join(problems[:5]) + (
            f"; +{len(problems) - 5} more" if len(problems) > 5 else ""
        )
        return MechanicalResult(VerificationStep(step="mechanical", passed=False, detail=shown), [])
    detail = f"{len(evidence)} citation(s) valid and seen by {session_id}"
    return MechanicalResult(
        VerificationStep(step="mechanical", passed=True, detail=detail), evidence
    )


def normalize_path(raw: str) -> str:
    """Model-written path → repo-relative POSIX form (`./a/../b.py` → `b.py`)."""
    text = raw.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return posixpath.normpath(text) if text else ""


def _citation(
    citation: Citation,
    session_id: str | None,
    reader: SnippetReader,
    ledger: SeenLines,
    listing: Mapping[str, FileEntry],
) -> CodeEvidence | str:
    path = normalize_path(citation.path)
    where = f"{citation.path}:{citation.start_line}-{citation.end_line}"
    try:
        resolve_in_snapshot(reader.root, path)
    except PathOutsideSnapshot:
        return f"{where}: outside the snapshot"
    entry = listing.get(path)
    if entry is None or not entry.readable:
        return f"{where}: not a readable file of the snapshot"
    lines = reader.lines(path)
    if lines is None:
        return f"{where}: file could not be read"
    start, end = citation.start_line, citation.end_line
    if not 1 <= start <= end <= len(lines):
        return f"{where}: lines outside the file (1-{len(lines)})"
    if end - start >= MAX_EVIDENCE_SPAN:
        return f"{where}: span of {end - start + 1} lines (max {MAX_EVIDENCE_SPAN})"
    if session_id is not None:
        unseen = ledger.missing(session_id, path, start, end)
        if unseen:
            sample = ", ".join(map(str, unseen[:5]))
            return f"{where}: {len(unseen)} line(s) never shown to the session ({sample})"
    evidence = reader.evidence(path, start, end)
    return evidence if evidence is not None else f"{where}: file could not be read"
