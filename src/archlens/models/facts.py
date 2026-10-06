"""Deterministic facts from scanners and extractors (DATA_MODEL.md §4)."""

from typing import Literal

from pydantic import JsonValue

from archlens.models.base import Contract
from archlens.models.enums import Severity
from archlens.models.evidence import Evidence

ToolStatus = Literal["ok", "error", "timeout", "skipped"]


class Fact(Contract):
    id: str  # f"{kind}:{sha1(source, kind, canonical attributes, evidence locs)[:12]}"
    kind: str
    source: str  # "gitleaks@8.x.y", "ast:imports", ...
    severity: Severity | None = None
    attributes: dict[str, JsonValue]
    evidence: list[Evidence]


class ToolRunRecord(Contract):
    tool: str
    version: str
    command: list[str]  # argv with paths replaced by placeholders
    status: ToolStatus
    duration_ms: int
    fact_count: int
    error: str | None = None


class FactSet(Contract):
    commit_sha: str
    facts: list[Fact]
    tool_runs: list[ToolRunRecord]

    def by_kind(self, *kinds: str) -> list[Fact]:
        """Facts whose kind is one of `kinds`, in their original order."""
        wanted = set(kinds)
        return [fact for fact in self.facts if fact.kind in wanted]
