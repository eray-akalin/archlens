"""Self-consistency for LLM checks (docs/LLM.md §4).

Checks with `self_consistency: n` (default 2 for critical checks) get n-1 extra sessions restricted
to them. All runs agree → the first result stands. Any disagreement → `unknown`,
`confidence="low"`, `reason="inconsistent"`, with every run's claim kept. A rerun that failed for
budget, model or output problems is not a disagreement: the first result stands with low
confidence.
"""

from collections.abc import Sequence

from archlens.evaluate.llm_session import Evaluator
from archlens.models import CheckResult, CheckSpec, Rubric

RUN_FAILURES = frozenset({"budget", "llm_error", "invalid_output"})


async def evaluate_llm_checks(
    evaluator: Evaluator, rubric: Rubric, checks: Sequence[CheckSpec]
) -> list[CheckResult]:
    """First session over all `checks`, then the consistency reruns; merged results in order."""
    if not checks:
        return []
    first = await evaluator.evaluate(rubric, checks, attempt=0)
    reruns: list[list[CheckResult]] = []
    for attempt in range(1, max(c.consistency_runs for c in checks)):
        subset = [c for c in checks if c.consistency_runs > attempt]
        reruns.append(await evaluator.evaluate(rubric, subset, attempt=attempt))
    return merge_runs(first, reruns)


def merge_runs(
    first: Sequence[CheckResult], reruns: Sequence[Sequence[CheckResult]]
) -> list[CheckResult]:
    others: dict[str, list[CheckResult]] = {}
    for run in reruns:
        for result in run:
            others.setdefault(result.check_id, []).append(result)
    merged: list[CheckResult] = []
    for result in first:
        alternatives = others.get(result.check_id, [])
        usable = [r for r in alternatives if r.reason not in RUN_FAILURES]
        if any(r.verdict != result.verdict for r in usable):
            runs = [result, *alternatives]
            claim = " | ".join(f"run {i + 1} {r.verdict}: {r.claim}" for i, r in enumerate(runs))
            merged.append(
                result.model_copy(
                    update={
                        "verdict": "unknown",
                        "confidence": "low",
                        "reason": "inconsistent",
                        "claim": claim,
                        "llm_call_ids": [i for r in runs for i in r.llm_call_ids],
                    }
                )
            )
        elif len(usable) < len(alternatives):
            merged.append(result.model_copy(update={"confidence": "low"}))
        else:
            merged.append(result)
    return merged
