"""What models return (DATA_MODEL.md §6).

Kept flat for strict structured outputs: no unions, no optional fields, no free-form dicts.
Length and count caps (claim ≤ 300 chars, ≤ 5 citations, rationale ≤ 200 chars) are applied in
post-processing, not here, so an over-long answer is trimmed instead of failing the parse.
Citation line ranges are validated by the verifier's mechanical step, never at parse time.
"""

from typing import Literal

from archlens.models.base import Contract
from archlens.models.enums import Confidence, Verdict


class Citation(Contract):
    path: str
    start_line: int
    end_line: int


class LLMCheckOutput(Contract):
    check_id: str
    verdict: Verdict
    claim: str
    citations: list[Citation]
    confidence: Confidence


class MetricEvaluationOutput(Contract):
    results: list[LLMCheckOutput]


class EntailmentItem(Contract):
    ref: str
    supports: Literal["yes", "no", "insufficient"]
    rationale: str


class EntailmentBatchOutput(Contract):
    items: list[EntailmentItem]  # one per finding in the batch; a missing item → unverified


class SkepticOutput(Contract):
    refuted: bool
    reason: str
    citations: list[Citation]
