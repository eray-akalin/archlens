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
