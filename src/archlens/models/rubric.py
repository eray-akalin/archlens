"""Rubric YAML contracts (RUBRICS.md §1) and the deterministic rule outcome (DATA_MODEL.md §5).

These models mirror the YAML schema exactly; unknown keys are rejected. Only the requirements
RUBRICS.md states are enforced here (a rule for deterministic checks, guidance for LLM checks,
absence probes where evidence-less verdicts are allowed); fields that don't apply to a check's type
are tolerated and ignored. Registry checks (unknown rule names, params) belong to the loader.
"""

from collections.abc import Mapping
from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_validator

from archlens.models.base import Contract
from archlens.models.enums import CheckType, EvidencePolicy, Severity, Verdict
from archlens.models.evidence import Evidence

_SEMVER = r"^\d+\.\d+\.\d+$"


class AbsenceProbe(Contract):
    """A pattern whose presence would contradict an evidence-less fail/partial/NA claim."""

    kind: Literal["path_glob", "regex", "symbol"]
    pattern: str
    path_glob: str | None = None


class AppliesWhen(Contract):
    """All present clauses must hold over RepoProfile.flags; `{}` always applies."""

    all: list[str] = Field(default_factory=list[str])
    any: list[str] = Field(default_factory=list[str])
    none: list[str] = Field(default_factory=list[str])

    def holds(self, flags: Mapping[str, bool]) -> bool:
        """Evaluate against profile flags; a flag missing from `flags` counts as False."""
        return (
            all(flags.get(f, False) for f in self.all)
            and (not self.any or any(flags.get(f, False) for f in self.any))
            and not any(flags.get(f, False) for f in self.none)
        )


class CheckSpec(Contract):
    id: Annotated[str, Field(pattern=r"^[A-Z]+-\d{2}$")]
    title: str
    type: CheckType
    severity: Severity
    weight: Annotated[float, Field(ge=0)] | None = None  # None → default from severity
    applies_when: AppliesWhen = Field(default_factory=AppliesWhen)
    rationale: str
    remediation: str
    # deterministic only
    rule: str | None = None
    params: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    # llm only
    guidance: str | None = None
    fact_kinds: list[str] = Field(default_factory=list[str])
    evidence_policy: EvidencePolicy = "positive_required"
    absence_probes: list[AbsenceProbe] = Field(default_factory=list[AbsenceProbe])
    na_allowed: bool = False
    self_consistency: Annotated[int, Field(ge=1)] | None = None  # None → 2 if critical, else 1
    retired: bool = False

    @property
    def consistency_runs(self) -> int:
        """Number of evaluator runs for this check (RUBRICS.md §1 default)."""
        if self.self_consistency is not None:
            return self.self_consistency
        return 2 if self.severity == "critical" else 1

    @model_validator(mode="after")
    def _check_type_fields(self) -> Self:
        if self.type == "deterministic" and not self.rule:
            raise ValueError(f"{self.id}: deterministic check needs `rule`")
        if self.type == "llm":
            if not self.guidance:
                raise ValueError(f"{self.id}: llm check needs `guidance`")
            needs_probes = self.evidence_policy == "absence_allowed" or self.na_allowed
            if needs_probes and not self.absence_probes:
                raise ValueError(
                    f"{self.id}: absence_allowed or na_allowed requires `absence_probes`"
                )
        return self


class Rubric(Contract):
    metric: Annotated[str, Field(pattern=r"^[a-z][a-z_]*$")]
    title: str
    version: Annotated[str, Field(pattern=_SEMVER)]
    weight: Annotated[float, Field(ge=0)]
    description: str
    applies_when: AppliesWhen = Field(default_factory=AppliesWhen)
    scope_globs: list[str] = Field(default_factory=lambda: ["**/*"])
    checks: list[CheckSpec]

    @model_validator(mode="after")
    def _check_unique_ids(self) -> Self:
        ids = [check.id for check in self.checks]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate check ids: {duplicates}")
        return self


class RuleOutcome(Contract):
    """What a deterministic rule returns; the registry wraps it into a CheckResult."""

    verdict: Verdict
    claim: str
    evidence: list[Evidence]
    reason: str | None = None
