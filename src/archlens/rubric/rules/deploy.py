"""`deploy.*` rules over `deploy_config` facts (PERF-06)."""

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

TOOL = "ast:deploy"


class LimitsParams(RuleParams):
    pass


@rule("deploy.resource_limits")
def resource_limits(ctx: RuleContext, params: LimitsParams) -> RuleOutcome:
    """Every compose service / k8s workload sets resource limits → pass; some → partial."""
    if (missing := missing_data(ctx, TOOL)) is not None:
        return missing
    configs = ctx.facts.by_kind("deploy_config")
    if not configs:
        return RuleOutcome(
            verdict="not_applicable", claim="No compose services or Kubernetes workloads.",
            evidence=[scan(ctx, TOOL, "deployment configs", 0)], reason="no_deploy_config",
        )  # fmt: skip
    unlimited = [c for c in configs if not attr_bool(c, "has_limits")]
    evidence = [
        scan(ctx, TOOL, "workloads without resource limits", len(unlimited)),
        *fact_evidence(unlimited or configs, MAX_EVIDENCE - 1),
    ]
    if not unlimited:
        return RuleOutcome(
            verdict="pass", claim=f"All {plural(len(configs), 'workload')} set resource limits.",
            evidence=evidence,
        )  # fmt: skip
    names = ", ".join(f"{attr_str(c, 'name')} ({attr_str(c, 'path')})" for c in unlimited[:3])
    return RuleOutcome(
        verdict="partial" if len(unlimited) < len(configs) else "fail",
        claim=f"{len(unlimited)} of {plural(len(configs), 'workload')} have no resource "
        f"limits: {names}.",
        evidence=evidence,
    )
