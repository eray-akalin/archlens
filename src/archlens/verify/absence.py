"""Step 3 — absence replay (docs/LLM.md §6). No LLM.

A check's `absence_probes` describe what would *contradict* an evidence-less fail/partial/NA
claim: signs that the thing the check is about exists. Zero hits across all probes → the step
passes with `ScanEvidence(tool="absence_probe", result_count=0)`. Any hit → the claim is
rejected and the hits come back as `CodeEvidence` so the report shows what was missed. A probe
that can't be run to completion (regex timeout, bad pattern, no symbol index) makes the step
inconclusive: absence can't be confirmed, so the finding stays unverified.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

import regex

from archlens import __version__, index
from archlens.evidence import SnippetReader
from archlens.globs import glob_match
from archlens.models import (
    MAX_EVIDENCE_SPAN,
    AbsenceProbe,
    Evidence,
    ScanEvidence,
    VerificationStep,
)
from archlens.tools.repo_tools import REGEX_TIMEOUT_S, RepoTools

MAX_HITS = 10
SYMBOL_POOL = 200  # definitions fetched before the probe's path_glob filter
TOOL = "absence_probe"
Outcome = Literal["absent", "found", "inconclusive"]


@dataclass(frozen=True)
class AbsenceResult:
    outcome: Outcome
    step: VerificationStep
    evidence: list[Evidence] = field(default_factory=list[Evidence])


def describe(probe: AbsenceProbe) -> str:
    scope = f" in {probe.path_glob}" if probe.path_glob else ""
    return f"{probe.kind}:{probe.pattern}{scope}"


async def replay(
    probes: Sequence[AbsenceProbe], tools: RepoTools, reader: SnippetReader
) -> AbsenceResult:
    """Run every probe over the snapshot. Never raises."""
    hits: list[Evidence] = []
    problems: list[str] = []
    for probe in probes:
        if len(hits) >= MAX_HITS:
            break
        if probe.kind == "path_glob":
            hits += _paths(probe, tools, reader, MAX_HITS - len(hits))
        elif probe.kind == "regex":
            found, problem = _regex(probe, tools, reader, MAX_HITS - len(hits))
            hits += found
            problems += [problem] if problem else []
        else:
            found, problem = await _symbols(probe, tools, reader, MAX_HITS - len(hits))
            hits += found
            problems += [problem] if problem else []
    queries = "; ".join(describe(p) for p in probes)
    if hits:
        detail = f"{len(hits)} probe hit(s) contradict the absence claim ({queries})"
        return AbsenceResult(
            "found", VerificationStep(step="absence", passed=False, detail=detail), hits
        )
    if problems or not probes:
        detail = "; ".join(problems) if problems else "the check has no absence probes"
        return AbsenceResult(
            "inconclusive", VerificationStep(step="absence", passed=False, detail=detail)
        )
    record = ScanEvidence(tool=TOOL, tool_version=__version__, query=queries, result_count=0)
    detail = f"no probe hits ({queries})"
    return AbsenceResult(
        "absent", VerificationStep(step="absence", passed=True, detail=detail), [record]
    )


def _paths(
    probe: AbsenceProbe, tools: RepoTools, reader: SnippetReader, limit: int
) -> list[Evidence]:
    out: list[Evidence] = []
    for entry in tools.listing():
        if len(out) >= limit:
            break
        if glob_match(entry.path, probe.pattern):
            code = reader.evidence(entry.path, 1) if entry.readable else None
            out.append(
                code
                or ScanEvidence(
                    tool=TOOL, tool_version=__version__, query=f"path {entry.path}", result_count=1
                )
            )
    return out


def _regex(
    probe: AbsenceProbe, tools: RepoTools, reader: SnippetReader, limit: int
) -> tuple[list[Evidence], str | None]:
    try:
        pattern = regex.compile(probe.pattern, regex.MULTILINE)
    except regex.error as exc:
        return [], f"{describe(probe)}: invalid regex ({exc})"
    out: list[Evidence] = []
    timeouts = 0
    for entry in tools.listing():
        if len(out) >= limit:
            break
        if not entry.readable or entry.is_vendored:
            continue
        if probe.path_glob and not glob_match(entry.path, probe.path_glob):
            continue
        lines = tools.redacted_lines(entry.path)
        if not lines:
            continue
        text = "\n".join(lines)
        try:
            match = pattern.search(text, timeout=REGEX_TIMEOUT_S)
        except TimeoutError:
            timeouts += 1
            continue
        if match is not None:
            line = text.count("\n", 0, match.start()) + 1
            code = reader.evidence(entry.path, line)
            if code is not None:
                out.append(code)
    problem = (
        f"{describe(probe)}: regex timed out on {timeouts} file(s)"
        if timeouts and not out
        else None
    )
    return out, problem


async def _symbols(
    probe: AbsenceProbe, tools: RepoTools, reader: SnippetReader, limit: int
) -> tuple[list[Evidence], str | None]:
    if tools.index_path is None:
        return [], f"{describe(probe)}: no symbol index"
    defs, _ = await index.find_symbols(tools.index_path, probe.pattern, SYMBOL_POOL)
    out: list[Evidence] = []
    for d in defs:
        if probe.path_glob and not glob_match(d.path, probe.path_glob):
            continue
        end = min(d.end_line, d.start_line + MAX_EVIDENCE_SPAN - 1)
        code = reader.evidence(d.path, d.start_line, end)
        if code is not None:
            out.append(code)
        if len(out) >= limit:
            break
    return out, None
