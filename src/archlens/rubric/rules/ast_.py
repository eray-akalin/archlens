"""`ast.*` rules over `print_call` / `log_call` facts (LOG-02)."""

from archlens.models import RuleOutcome
from archlens.rubric.context import RuleContext
from archlens.rubric.registry import RuleParams, rule
from archlens.rubric.rules._common import MAX_EVIDENCE, attr_bool, fact_evidence, missing_data, scan

TOOL = "ast:logging"


class DebugOutputParams(RuleParams):
    pass_below: float = 0.05
    partial_below: float = 0.2


@rule("ast.debug_output_ratio")
def debug_output_ratio(ctx: RuleContext, params: DebugOutputParams) -> RuleOutcome:
    """Non-test print/console calls / (prints + log calls); NA when there are neither."""
    if (missing := missing_data(ctx, TOOL)) is not None:
        return missing
    prints = [f for f in ctx.facts.by_kind("print_call") if not attr_bool(f, "in_test")]
    logs = [f for f in ctx.facts.by_kind("log_call") if not attr_bool(f, "in_test")]
    total = len(prints) + len(logs)
    query = "print/console calls in production code"
    if total == 0:
        return RuleOutcome(
            verdict="not_applicable", claim="No print or log calls in production code.",
            evidence=[scan(ctx, TOOL, query, 0)], reason="no_output_calls",
        )  # fmt: skip
    share = len(prints) / total
    if share < params.pass_below:
        verdict = "pass"
    elif share < params.partial_below:
        verdict = "partial"
    else:
        verdict = "fail"
    return RuleOutcome(
        verdict=verdict,
        claim=f"{len(prints)} print/console calls vs {len(logs)} log calls in production code "
        f"({share:.0%} of output calls).",
        evidence=[scan(ctx, TOOL, query, len(prints)), *fact_evidence(prints, MAX_EVIDENCE - 1)],
    )
