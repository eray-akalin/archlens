"""`deps.*` rules over `manifest` facts (STR-06)."""

from archlens.models import RuleOutcome
from archlens.rubric.context import RuleContext
from archlens.rubric.registry import RuleParams, rule
from archlens.rubric.rules._common import (
    MAX_EVIDENCE,
    attr_bool,
    attr_str,
    fact_evidence,
    missing_data,
    plural,
    scan,
)

TOOL = "ast:manifests"


class LockfileParams(RuleParams):
    pass


@rule("deps.lockfile_present")
def lockfile_present(ctx: RuleContext, params: LockfileParams) -> RuleOutcome:
    """Every manifest has a lockfile (or pins every dependency) → pass, else fail; NA without
    manifests."""
    if (missing := missing_data(ctx, TOOL)) is not None:
        return missing
    manifests = ctx.facts.by_kind("manifest")
    if not manifests:
        return RuleOutcome(
            verdict="not_applicable", claim="No dependency manifests found.",
            evidence=[scan(ctx, TOOL, "manifests", 0)], reason="no_manifests",
        )  # fmt: skip
    unlocked = [m for m in manifests if not attr_bool(m, "has_lockfile")]
    evidence = [
        scan(ctx, TOOL, "manifests without a lockfile", len(unlocked)),
        *fact_evidence(unlocked or manifests, MAX_EVIDENCE - 1),
    ]
    if not unlocked:
        return RuleOutcome(
            verdict="pass",
            claim=f"All {plural(len(manifests), 'manifest')} are locked.",
            evidence=evidence,
        )
    paths = ", ".join(attr_str(m, "path") or "?" for m in unlocked[:3])
    return RuleOutcome(
        verdict="fail",
        claim=f"{len(unlocked)} of {plural(len(manifests), 'manifest')} have no lockfile and "
        f"unpinned dependencies: {paths}.",
        evidence=evidence,
    )
