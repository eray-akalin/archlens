"""Hand-built rubrics and findings for scorer tests."""

from archlens.models import (
    CheckResult,
    CheckSpec,
    CheckType,
    Finding,
    RepoProfile,
    Rubric,
    Verdict,
    Verification,
    VerificationStatus,
    finding_id,
    is_scorable,
)

SHA = "a" * 40


def check(
    check_id: str,
    severity: str = "medium",
    *,
    weight: float | None = None,
    applies_when: dict[str, list[str]] | None = None,
    retired: bool = False,
) -> CheckSpec:
    return CheckSpec.model_validate(
        {
            "id": check_id, "title": "t", "type": "deterministic", "severity": severity,
            "weight": weight, "applies_when": applies_when or {}, "rationale": "r",
            "remediation": "r", "rule": "files.any_exists", "retired": retired,
        }
    )  # fmt: skip


def rubric(
    *checks: CheckSpec,
    metric: str = "demo",
    weight: float = 1.0,
    applies_when: dict[str, list[str]] | None = None,
) -> Rubric:
    return Rubric.model_validate(
        {
            "metric": metric, "title": metric, "version": "1.0.0", "weight": weight,
            "description": "d", "applies_when": applies_when or {}, "checks": list(checks),
        }
    )  # fmt: skip


def finding(
    check_id: str,
    verdict: Verdict,
    status: VerificationStatus = "verified",
    *,
    metric: str = "demo",
    origin: CheckType = "llm",
) -> Finding:
    result = CheckResult(
        check_id=check_id, metric=metric, origin=origin, verdict=verdict, claim="c",
        confidence="high", rubric_version="1.0.0",
    )  # fmt: skip
    verification = Verification(status=status, steps=[])
    return Finding(
        id=finding_id(check_id, SHA),
        result=result,
        verification=verification,
        scored=is_scorable(result, verification),
    )


def profile(**flags: bool) -> RepoProfile:
    return RepoProfile(
        languages={}, frameworks=[], package_managers=[], ci_systems=[], test_frameworks=[],
        flags=flags, entrypoints=[],
    )  # fmt: skip
