"""`sast.*` rules over semgrep `sast_finding` facts (SEC-03)."""

from pydantic import Field
from wcmatch import fnmatch

from archlens.models import CodeEvidence, Evidence, Fact, RuleOutcome, Severity
from archlens.rubric.context import RuleContext
from archlens.rubric.registry import RuleParams, rule
from archlens.rubric.rules._common import attr_str, max_severity_outcome, missing_data

TOOL = "semgrep"


class MaxSeverityParams(RuleParams):
    fail_at: Severity = "high"
    partial_at: Severity | None = "medium"
    exclude_rule_globs: list[str] = Field(default_factory=list[str])


def _location(evidence: list[Evidence]) -> str:
    code = next((e for e in evidence if isinstance(e, CodeEvidence)), None)
    return f" at {code.path}:{code.start_line}" if code else ""


def _describe(fact: Fact) -> str:
    rule_id = (attr_str(fact, "rule_id") or "?").rsplit(".", 1)[-1]
    return f"{rule_id}{_location(fact.evidence)} ({fact.severity})"


@rule("sast.max_severity")
def max_severity(ctx: RuleContext, params: MaxSeverityParams) -> RuleOutcome:
    """Worst `sast_finding` severity, ignoring rule IDs matching `exclude_rule_globs`."""
    if (missing := missing_data(ctx, TOOL)) is not None:
        return missing
    findings = [
        f
        for f in ctx.facts.by_kind("sast_finding")
        if not (
            params.exclude_rule_globs
            and fnmatch.fnmatch(attr_str(f, "rule_id") or "", params.exclude_rule_globs)
        )
    ]
    return max_severity_outcome(
        ctx,
        tool=TOOL,
        findings=findings,
        fail_at=params.fail_at,
        partial_at=params.partial_at,
        noun="static-analysis finding",
        describe=_describe,
    )
