"""`complexity.*` rules over lizard `function_metrics` facts (STR-03)."""

from pydantic import Field

from archlens.models import RuleOutcome
from archlens.rubric.context import RuleContext
from archlens.rubric.registry import RuleParams, rule
from archlens.rubric.rules._common import (
    MAX_EVIDENCE,
    attr_int,
    attr_str,
    fact_evidence,
    missing_data,
    scan,
)

TOOL = "lizard"


class CcnRatioParams(RuleParams):
    ccn: int = Field(default=15, ge=1)
    pass_below: float = 0.03
    partial_below: float = 0.08


@rule("complexity.ccn_ratio")
def ccn_ratio(ctx: RuleContext, params: CcnRatioParams) -> RuleOutcome:
    """Share of functions with cyclomatic complexity over `ccn`."""
    if (missing := missing_data(ctx, TOOL)) is not None:
        return missing
    functions = ctx.facts.by_kind("function_metrics")
    if not functions:
        return RuleOutcome(
            verdict="not_applicable", claim="lizard measured no functions.",
            evidence=[scan(ctx, TOOL, "functions", 0)], reason="no_functions",
        )  # fmt: skip
    complex_ = sorted(
        (f for f in functions if attr_int(f, "ccn") > params.ccn), key=lambda f: -attr_int(f, "ccn")
    )
    share = len(complex_) / len(functions)
    if share < params.pass_below:
        verdict = "pass"
    elif share < params.partial_below:
        verdict = "partial"
    else:
        verdict = "fail"
    worst = ", ".join(f"{attr_str(f, 'name')} ({attr_int(f, 'ccn')})" for f in complex_[:3])
    detail = f"; most complex: {worst}" if complex_ else ""
    return RuleOutcome(
        verdict=verdict,
        claim=f"{len(complex_)} of {len(functions)} functions ({share:.1%}) exceed CCN "
        f"{params.ccn}{detail}.",
        evidence=[
            scan(ctx, TOOL, f"functions with CCN > {params.ccn}", len(complex_)),
            *fact_evidence(complex_, MAX_EVIDENCE - 1),
        ],
    )
