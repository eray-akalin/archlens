"""`iac.*` rules over checkov `iac_finding` and `iac_parse_error` facts (CTR-06)."""

from pydantic import Field

from archlens.models import RuleOutcome, Severity, severity_rank
from archlens.rubric.context import RuleContext
from archlens.rubric.registry import RuleParams, rule
from archlens.rubric.rules._common import (
    MAX_EVIDENCE,
    SEVERITY_RANK,
    attr_str,
    fact_evidence,
    missing_data,
    plural,
    scan,
)

TOOL = "checkov"


class MaxSeverityParams(RuleParams):
    fail_at: Severity = "high"
    unknown_severity_fail_count: int = Field(default=5, ge=1)


@rule("iac.max_severity")
def max_severity(ctx: RuleContext, params: MaxSeverityParams) -> RuleOutcome:
    """fail on any finding ≥ `fail_at` or ≥ N findings without a severity (checkov OSS often
    omits it); partial on any other finding; pass on none — unless checkov could not parse some
    IaC files: then unknown (`iac_unparsed`), since those files were never checked."""
    if (missing := missing_data(ctx, TOOL)) is not None:
        return missing
    findings = ctx.facts.by_kind("iac_finding")
    parse_errors = ctx.facts.by_kind("iac_parse_error")
    unparsed = sorted({attr_str(f, "path") or "?" for f in parse_errors})
    shown = ", ".join(unparsed[:5]) + (", ..." if len(unparsed) > 5 else "")
    note = f"{plural(len(unparsed), 'IaC file')} could not be parsed and went unchecked ({shown})"
    severe = [f for f in findings if severity_rank(f.severity) >= SEVERITY_RANK[params.fail_at]]
    unrated = [f for f in findings if f.severity is None]
    evidence = [
        scan(ctx, TOOL, "IaC misconfigurations", len(findings)),
        *fact_evidence(severe or findings, MAX_EVIDENCE - 1),
    ]
    if not findings and unparsed:
        return RuleOutcome(
            verdict="unknown",
            claim=f"checkov: {note}; no misconfigurations in the files it parsed.",
            evidence=[*evidence, *fact_evidence(parse_errors, MAX_EVIDENCE - 1)],
            reason="iac_unparsed",
        )
    if not findings:
        return RuleOutcome(
            verdict="pass", claim="checkov found no misconfigurations.", evidence=evidence
        )
    also = f" {note}." if unparsed else ""
    checks = ", ".join(sorted({attr_str(f, "check_id") or "?" for f in severe or findings})[:4])
    if severe or len(unrated) >= params.unknown_severity_fail_count:
        why = (
            f"{plural(len(severe), 'finding')} at or above {params.fail_at}"
            if severe
            else f"{len(unrated)} findings without a severity"
        )
        return RuleOutcome(
            verdict="fail", claim=f"checkov: {why} ({checks}).{also}", evidence=evidence
        )
    return RuleOutcome(
        verdict="partial",
        claim=f"checkov: {plural(len(findings), 'lower-severity finding')} ({checks}).{also}",
        evidence=evidence,
    )
