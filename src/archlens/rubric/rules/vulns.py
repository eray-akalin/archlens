"""`vulns.*` rules over osv-scanner `vuln_dependency` facts (SEC-02)."""

from archlens.models import Fact, RuleOutcome, Severity
from archlens.rubric.context import RuleContext
from archlens.rubric.registry import RuleParams, rule
from archlens.rubric.rules._common import attr_list, attr_str, max_severity_outcome, missing_data

TOOL = "osv-scanner"


class MaxSeverityParams(RuleParams):
    fail_at: Severity = "high"
    partial_at: Severity | None = "medium"


def _describe(fact: Fact) -> str:
    ids = attr_list(fact, "ids")
    advisory = f" {ids[0]}" if ids else ""
    return f"{attr_str(fact, 'package')} {attr_str(fact, 'version')}{advisory} ({fact.severity})"


@rule("vulns.max_severity")
def max_severity(ctx: RuleContext, params: MaxSeverityParams) -> RuleOutcome:
    """Worst `vuln_dependency` severity against `fail_at` / `partial_at`."""
    if (missing := missing_data(ctx, TOOL)) is not None:
        return missing
    return max_severity_outcome(
        ctx,
        tool=TOOL,
        findings=ctx.facts.by_kind("vuln_dependency"),
        fail_at=params.fail_at,
        partial_at=params.partial_at,
        noun="vulnerable dependency",
        describe=_describe,
    )
