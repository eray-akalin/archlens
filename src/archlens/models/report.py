"""Scores, cost and the canonical report (DATA_MODEL.md §8)."""

from typing import Annotated, Self

from pydantic import AwareDatetime, Field, model_validator

from archlens.models.base import SCHEMA_VERSION, Contract
from archlens.models.enums import MetricStatus, Verdict
from archlens.models.facts import ToolRunRecord
from archlens.models.results import Finding
from archlens.models.snapshot import RepoProfile

Score = Annotated[float, Field(ge=0.0, le=10.0)]


class MetricScore(Contract):
    metric: str
    status: MetricStatus
    score: Score | None  # one decimal; None unless status == "scored"
    coverage: Annotated[float, Field(ge=0.0, le=1.0)]  # scored weight / applicable weight
    capped_by: list[str]  # check IDs that triggered the critical cap
    counts: dict[Verdict, int]

    @model_validator(mode="after")
    def _check_score(self) -> Self:
        if (self.score is None) != (self.status != "scored"):
            raise ValueError("score must be set iff status == 'scored'")
        return self


class LLMCallRecord(Contract):
    id: str  # ULID
    stage: str
    metric: str | None
    model: str
    prompt_version: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int  # includes reasoning tokens
    reasoning_tokens: int
    latency_ms: int
    cost_usd: float
    cache_hit: bool  # exact-cache hit (no provider call)
    attempt: int  # retry index


class CostSummary(Contract):
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    usd: float
    by_stage: dict[str, float]
    by_metric: dict[str, float]


class MetricNarrative(Contract):
    metric: str
    text: str


class Narrative(Contract):
    """LLM-facing (synthesizer output): no dicts, for strict structured outputs."""

    executive_summary: str
    per_metric: list[MetricNarrative]
    cited_findings: list[str]  # must all exist in findings


class ConfigFingerprint(Contract):
    archlens_version: str
    rubric_versions: dict[str, str]
    prompt_versions: dict[str, str]
    models: dict[str, str]  # role -> deployment
    tool_versions: dict[str, str]


class AssessmentReport(Contract):
    """The canonical output (`assessment.json`). Every rendering is derived from it."""

    schema_version: str = SCHEMA_VERSION
    run_id: str
    repo_url: str | None
    commit_sha: str
    created_at: AwareDatetime
    config: ConfigFingerprint
    profile: RepoProfile
    metric_scores: list[MetricScore]
    overall_score: Score | None
    findings: list[Finding]  # scored == True
    other_findings: list[Finding]  # unverified, rejected, disputed, unknown, not_applicable
    tool_runs: list[ToolRunRecord]
    cost: CostSummary
    timings_ms: dict[str, int]  # per stage
    narrative: Narrative | None

    @model_validator(mode="after")
    def _check_finding_sections(self) -> Self:
        if not all(f.scored for f in self.findings):
            raise ValueError("`findings` may only hold scored findings")
        if any(f.scored for f in self.other_findings):
            raise ValueError("`other_findings` may not hold scored findings")
        return self
