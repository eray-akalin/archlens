"""Ablation switches applied to rubrics (EVALUATION.md §6): the pipeline and the cost projection
use the same transformed rubrics, so a config's projection matches what it runs.

- `deterministic_rules=False`: every deterministic check goes to the evaluator instead, with
  guidance generated from the check's title, rationale and its rule's docstring.
- `self_consistency=False`: one evaluator run per check (no reruns, no tie-break).
"""

from collections.abc import Mapping

from archlens.models import CheckSpec, Rubric
from archlens.rubric import get_rule


def ablate(
    rubrics: Mapping[str, Rubric],
    *,
    deterministic_rules: bool = True,
    self_consistency: bool = True,
) -> dict[str, Rubric]:
    """`rubrics` with the switched-off features removed; the same objects when nothing is off."""
    if deterministic_rules and self_consistency:
        return dict(rubrics)
    out: dict[str, Rubric] = {}
    for name, rubric in rubrics.items():
        checks = list(rubric.checks)
        if not deterministic_rules:
            checks = [judged_by_llm(c) if c.type == "deterministic" else c for c in checks]
        if not self_consistency:
            checks = [c.model_copy(update={"self_consistency": 1}) for c in checks]
        out[name] = rubric.model_copy(update={"checks": checks})
    return out


def judged_by_llm(check: CheckSpec) -> CheckSpec:
    """A deterministic check as an LLM check (positive evidence required, no facts)."""
    registered = get_rule(check.rule or "")
    doc = " ".join((registered.func.__doc__ or "").split()) if registered else ""
    guidance = "\n".join(
        [
            f"Judge whether the repository meets: {check.title}.",
            f"Why it matters: {' '.join(check.rationale.split())}",
            *([f"What an automated rule would check: {doc}"] if doc else []),
            "- pass: the repository clearly meets it; cite the code or configuration showing it.",
            "- partial: it is partly met; cite what exists and say what is missing.",
            "- fail: it is not met; cite the code or configuration that shows the problem.",
            "If you find no evidence either way, answer unknown.",
        ]
    )
    return CheckSpec.model_validate(
        {
            **check.model_dump(exclude={"rule", "params", "self_consistency"}),
            "type": "llm",
            "guidance": guidance,
            "fact_kinds": [],
            "evidence_policy": "positive_required",
            "absence_probes": [],
            "na_allowed": False,
        }
    )
