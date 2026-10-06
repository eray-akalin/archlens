"""`secrets.*` rules over gitleaks `secret` facts (SEC-01)."""

from pydantic import Field

from archlens.globs import glob_match
from archlens.models import RuleOutcome
from archlens.rubric.context import RuleContext
from archlens.rubric.registry import RuleParams, rule
from archlens.rubric.rules._common import attr_str, fact_evidence, missing_data, plural, scan

TOOL = "gitleaks"


class NoneFoundParams(RuleParams):
    exclude_globs: list[str] = Field(default_factory=lambda: ["**/test*/**", "**/*example*"])


@rule("secrets.none_found")
def none_found(ctx: RuleContext, params: NoneFoundParams) -> RuleOutcome:
    """fail if gitleaks found a secret outside `exclude_globs`, else pass."""
    if (missing := missing_data(ctx, TOOL)) is not None:
        return missing
    secrets = ctx.facts.by_kind("secret")
    found = [f for f in secrets if not glob_match(attr_str(f, "path") or "", params.exclude_globs)]
    excluded = len(secrets) - len(found)
    if not found:
        note = f" ({excluded} in excluded paths)" if excluded else ""
        return RuleOutcome(
            verdict="pass",
            claim=f"gitleaks found no secrets outside excluded paths{note}.",
            evidence=[scan(ctx, TOOL, f"secrets outside {params.exclude_globs}", 0)],
        )
    files = sorted({attr_str(f, "path") or "" for f in found})
    rules = sorted({attr_str(f, "rule_id") or "" for f in found})
    return RuleOutcome(
        verdict="fail",
        claim=(
            f"gitleaks found {plural(len(found), 'secret')} in {plural(len(files), 'file')} "
            f"({', '.join(rules[:3])}): {', '.join(files[:3])}."
        ),
        evidence=fact_evidence(found),
    )
