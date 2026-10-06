"""Shared enums (DATA_MODEL.md §1)."""

from typing import Literal

Verdict = Literal["pass", "partial", "fail", "not_applicable", "unknown"]
Severity = Literal["critical", "high", "medium", "low", "info"]
Confidence = Literal["low", "medium", "high"]
CheckType = Literal["deterministic", "llm"]
EvidencePolicy = Literal["positive_required", "absence_allowed"]
VerificationStatus = Literal["verified", "unverified", "rejected", "disputed"]
MetricStatus = Literal["scored", "insufficient_evidence", "not_applicable"]

SCORABLE_VERDICTS: frozenset[Verdict] = frozenset({"pass", "partial", "fail"})

SEVERITY_RANK: dict[Severity, int] = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def severity_rank(severity: Severity | None) -> int:
    """Rank for comparisons; an unknown severity ranks below `info`."""
    return -1 if severity is None else SEVERITY_RANK[severity]
