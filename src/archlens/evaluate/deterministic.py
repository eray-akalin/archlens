"""Which checks of a rubric run, and the deterministic ones run here (ARCHITECTURE.md §2.4.1)."""

from collections.abc import Mapping

from archlens.models import CheckResult, CheckSpec, Rubric
from archlens.rubric import RuleContext, run_rule


def applicable(check: CheckSpec, flags: Mapping[str, bool]) -> bool:
    """Not retired and its `applies_when` holds for the profile flags."""
    return not check.retired and check.applies_when.holds(flags)


def llm_checks(rubric: Rubric, flags: Mapping[str, bool]) -> list[CheckSpec]:
    """Applicable LLM checks, in rubric order."""
    return [c for c in rubric.checks if c.type == "llm" and applicable(c, flags)]


def run_deterministic_checks(rubric: Rubric, ctx: RuleContext) -> list[CheckResult]:
    """Results of every applicable deterministic check, in rubric order. Never raises."""
    return [
        run_rule(check, rubric, ctx)
        for check in rubric.checks
        if check.type == "deterministic" and applicable(check, ctx.profile.flags)
    ]
