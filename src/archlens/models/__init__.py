"""Pydantic contracts between pipeline stages (docs/DATA_MODEL.md)."""

from archlens.models.base import SCHEMA_VERSION, Contract, MutableContract
from archlens.models.enums import (
    SCORABLE_VERDICTS,
    CheckType,
    Confidence,
    EvidencePolicy,
    MetricStatus,
    Severity,
    Verdict,
    VerificationStatus,
)
from archlens.models.evidence import MAX_EVIDENCE_SPAN, CodeEvidence, Evidence, ScanEvidence
from archlens.models.facts import Fact, FactSet, ToolRunRecord, ToolStatus
from archlens.models.llm_output import (
    Citation,
    EntailmentBatchOutput,
    EntailmentItem,
    LLMCheckOutput,
    MetricEvaluationOutput,
    SkepticOutput,
)
from archlens.models.report import (
    AssessmentReport,
    ConfigFingerprint,
    CostSummary,
    LLMCallRecord,
    MetricNarrative,
    MetricScore,
    Narrative,
)
from archlens.models.results import (
    CheckResult,
    Finding,
    SearchRecord,
    Verification,
    VerificationStep,
    finding_id,
    is_scorable,
)
from archlens.models.rubric import AbsenceProbe, AppliesWhen, CheckSpec, Rubric, RuleOutcome
from archlens.models.run_state import RunState, RunStatus, StageState, StageStatus
from archlens.models.snapshot import FileEntry, IngestLimits, RepoProfile, RepoSnapshot

__all__ = [
    "MAX_EVIDENCE_SPAN",
    "SCHEMA_VERSION",
    "SCORABLE_VERDICTS",
    "AbsenceProbe",
    "AppliesWhen",
    "AssessmentReport",
    "CheckResult",
    "CheckSpec",
    "CheckType",
    "Citation",
    "CodeEvidence",
    "Confidence",
    "ConfigFingerprint",
    "Contract",
    "CostSummary",
    "EntailmentBatchOutput",
    "EntailmentItem",
    "Evidence",
    "EvidencePolicy",
    "Fact",
    "FactSet",
    "FileEntry",
    "Finding",
    "IngestLimits",
    "LLMCallRecord",
    "LLMCheckOutput",
    "MetricEvaluationOutput",
    "MetricNarrative",
    "MetricScore",
    "MetricStatus",
    "MutableContract",
    "Narrative",
    "RepoProfile",
    "RepoSnapshot",
    "Rubric",
    "RuleOutcome",
    "RunState",
    "RunStatus",
    "ScanEvidence",
    "SearchRecord",
    "Severity",
    "SkepticOutput",
    "StageState",
    "StageStatus",
    "ToolRunRecord",
    "ToolStatus",
    "Verdict",
    "Verification",
    "VerificationStatus",
    "VerificationStep",
    "finding_id",
    "is_scorable",
]
