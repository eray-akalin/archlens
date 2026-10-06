"""Check results, verification and findings (DATA_MODEL.md §7)."""

from typing import Literal, Self

from pydantic import Field, JsonValue, model_validator

from archlens.models.base import Contract
from archlens.models.enums import (
    SCORABLE_VERDICTS,
    CheckType,
    Confidence,
    Verdict,
    VerificationStatus,
)
from archlens.models.evidence import Evidence
from archlens.models.llm_output import Citation


class SearchRecord(Contract):
    """One tool call an evaluator made."""

    tool: str  # "search_code", "read_file", "list_dir", "find_symbol", "get_facts"
    args: dict[str, JsonValue]
    result_count: int
    paths: list[str]  # up to 20 result paths


class CheckResult(Contract):
    check_id: str
    metric: str
    origin: CheckType
    verdict: Verdict
    claim: str
    citations: list[Citation] = Field(default_factory=list[Citation])  # raw, unvalidated
    evidence: list[Evidence] = Field(default_factory=list[Evidence])
    confidence: Confidence
    reason: str | None = None  # why unknown / not_applicable
    search_log: list[SearchRecord] = Field(default_factory=list[SearchRecord])
    llm_call_ids: list[str] = Field(default_factory=list[str])
    session_id: str | None = None  # keys the seen-lines ledger
    rubric_version: str
    prompt_version: str | None = None
    model: str | None = None
    attempt: int = 0  # self-consistency index


class VerificationStep(Contract):
    step: Literal["mechanical", "entailment", "absence", "skeptic"]
    passed: bool
    detail: str
    llm_call_id: str | None = None


class Verification(Contract):
    status: VerificationStatus
    steps: list[VerificationStep]


def finding_id(check_id: str, commit_sha: str) -> str:
    """Deterministic finding ID: `<check_id>@<first 12 chars of the commit SHA>`."""
    return f"{check_id}@{commit_sha[:12]}"


def is_scorable(result: CheckResult, verification: Verification) -> bool:
    """True iff the finding may affect a score: verified, with verdict pass/partial/fail."""
    return verification.status == "verified" and result.verdict in SCORABLE_VERDICTS


class Finding(Contract):
    id: str
    result: CheckResult
    verification: Verification
    scored: bool

    @model_validator(mode="after")
    def _check_scored(self) -> Self:
        if self.scored != is_scorable(self.result, self.verification):
            raise ValueError("scored must be True iff verified with verdict pass/partial/fail")
        return self
