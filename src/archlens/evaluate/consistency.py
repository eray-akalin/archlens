"""Self-consistency for LLM checks (docs/LLM.md §4).

Checks with `self_consistency: n` (default 2 for critical checks) get n-1 extra sessions restricted
to them. The usable runs (a run that failed for budget, model or output problems doesn't count)
decide by strict majority. A check whose runs have no majority gets one tie-break session on the
`tiebreak` role (a stronger setting, used only where the evaluator disagreed with itself); still
no majority → `unknown`, `confidence="low"`, `reason="inconsistent"`, with every run's claim kept.

Unanimous → the first usable result stands (low confidence if some run failed). Majority → the
first result with the majority verdict stands, with its own citations and session (its citations
are checked against that session's ledger), at most `medium` confidence.
"""

from collections import Counter
from collections.abc import Sequence

from archlens.evaluate.llm_session import Evaluator
from archlens.models import CheckResult, CheckSpec, Rubric

RUN_FAILURES = frozenset({"budget", "llm_error", "invalid_output"})


async def evaluate_llm_checks(
    evaluator: Evaluator, rubric: Rubric, checks: Sequence[CheckSpec]
) -> list[CheckResult]:
    """First session over all `checks`, the consistency reruns, then one tie-break session for
    checks without a majority; merged results in order."""
    if not checks:
        return []
    first = await evaluator.evaluate(rubric, checks, attempt=0)
    reruns: list[list[CheckResult]] = []
    runs = max(c.consistency_runs for c in checks)
    for attempt in range(1, runs):
        subset = [c for c in checks if c.consistency_runs > attempt]
        reruns.append(await evaluator.evaluate(rubric, subset, attempt=attempt))
    split = undecided(first, reruns)
    if split:
        subset = [c for c in checks if c.id in split]
        reruns.append(await evaluator.evaluate(rubric, subset, attempt=runs, role="tiebreak"))
    return merge_runs(first, reruns)


def _runs_by_check(
    first: Sequence[CheckResult], reruns: Sequence[Sequence[CheckResult]]
) -> dict[str, list[CheckResult]]:
    runs: dict[str, list[CheckResult]] = {r.check_id: [r] for r in first}
    for run in reruns:
        for result in run:
            runs.setdefault(result.check_id, []).append(result)
    return runs


def _majority(results: Sequence[CheckResult]) -> str | None:
    counts = Counter(r.verdict for r in results)
    verdict, top = counts.most_common(1)[0]
    return verdict if top * 2 > len(results) else None


def undecided(first: Sequence[CheckResult], reruns: Sequence[Sequence[CheckResult]]) -> set[str]:
    """Checks with at least two usable runs and no strict-majority verdict."""
    out: set[str] = set()
    for check_id, runs in _runs_by_check(first, reruns).items():
        usable = [r for r in runs if r.reason not in RUN_FAILURES]
        if len(usable) >= 2 and _majority(usable) is None:
            out.add(check_id)
    return out


def merge_runs(
    first: Sequence[CheckResult], reruns: Sequence[Sequence[CheckResult]]
) -> list[CheckResult]:
    by_check = _runs_by_check(first, reruns)
    merged: list[CheckResult] = []
    for result in first:
        runs = by_check[result.check_id]
        usable = [r for r in runs if r.reason not in RUN_FAILURES]
        if len(runs) == 1 or not usable:
            merged.append(result)
            continue
        verdict = _majority(usable)
        if verdict is None:
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
            continue
        chosen = next(r for r in usable if r.verdict == verdict)
        if len(usable) < len(runs):
            confidence = "low"
        elif all(r.verdict == verdict for r in usable):
            confidence = chosen.confidence
        else:
            confidence = "low" if chosen.confidence == "low" else "medium"
        merged.append(
            chosen
            if confidence == chosen.confidence
            else chosen.model_copy(update={"confidence": confidence})
        )
    return merged
