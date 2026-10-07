"""`size.*` rules over `file_metrics` facts (STR-02)."""

from pydantic import Field

from archlens.models import Evidence, RuleOutcome
from archlens.rubric.context import RuleContext
from archlens.rubric.registry import RuleParams, rule
from archlens.rubric.rules._common import (
    MAX_EVIDENCE,
    attr_bool,
    attr_int,
    attr_str,
    missing_data,
    plural,
    scan,
)

TOOL = "ast:metrics"


class MaxFileLocParams(RuleParams):
    threshold: int = Field(default=1000, ge=1)
    partial_max_offenders: int = Field(default=2, ge=0)


@rule("size.max_file_loc")
def max_file_loc(ctx: RuleContext, params: MaxFileLocParams) -> RuleOutcome:
    """Non-generated files over `threshold` LOC: none → pass, ≤ N → partial, more → fail."""
    if (missing := missing_data(ctx, TOOL)) is not None:
        return missing
    files = [f for f in ctx.facts.by_kind("file_metrics") if not attr_bool(f, "is_generated")]
    if not files:
        return RuleOutcome(
            verdict="not_applicable", claim="No source files to measure.",
            evidence=[scan(ctx, TOOL, "source files", 0)], reason="no_source_code",
        )  # fmt: skip
    over = sorted(
        (f for f in files if attr_int(f, "loc") > params.threshold),
        key=lambda f: -attr_int(f, "loc"),
    )
    evidence: list[Evidence] = [scan(ctx, TOOL, f"files over {params.threshold} LOC", len(over))]
    for f in over[: MAX_EVIDENCE - 1]:
        path = attr_str(f, "path") or ""
        if (code := ctx.evidence(path, 1)) is not None:
            evidence.append(code)
    if not over:
        return RuleOutcome(
            verdict="pass",
            claim=f"No non-generated source file exceeds {params.threshold} LOC "
            f"({plural(len(files), 'file')} measured).",
            evidence=evidence,
        )
    names = ", ".join(f"{attr_str(f, 'path')} ({attr_int(f, 'loc')})" for f in over[:3])
    verdict = "partial" if len(over) <= params.partial_max_offenders else "fail"
    return RuleOutcome(
        verdict=verdict,
        claim=f"{plural(len(over), 'file')} over {params.threshold} LOC: {names}.",
        evidence=evidence,
    )
