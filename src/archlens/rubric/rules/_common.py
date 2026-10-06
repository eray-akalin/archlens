"""Helpers shared by rule families: missing-data semantics, evidence, typed fact attributes."""

from collections.abc import Callable, Iterable

from pydantic import JsonValue

from archlens import __version__
from archlens.models import (
    SEVERITY_RANK,
    Evidence,
    Fact,
    RuleOutcome,
    ScanEvidence,
    Severity,
    severity_rank,
)
from archlens.rubric.context import RuleContext

MAX_EVIDENCE = 20  # evidence items per outcome; claims state the full counts


def missing_data(ctx: RuleContext, tool: str) -> RuleOutcome | None:
    """RUBRICS.md §5 for the tool (scanner or extractor) a rule depends on.

    `skipped` (no input files) → not_applicable; `error`/`timeout` → unknown with that reason;
    no run record at all → unknown (`tool_not_run`); `ok` → None (the rule proceeds).
    """
    run = ctx.tool_run(tool)
    if run is None:
        return RuleOutcome(
            verdict="unknown",
            claim=f"{tool} did not run, so this cannot be judged.",
            evidence=[],
            reason="tool_not_run",
        )
    if run.status == "skipped":
        return RuleOutcome(
            verdict="not_applicable",
            claim=f"{tool} had no input files in this repository.",
            evidence=[scan(ctx, tool, "input files", 0)],
            reason="tool_skipped",
        )
    if run.status in ("error", "timeout"):
        detail = f": {run.error[:200]}" if run.error else ""
        return RuleOutcome(
            verdict="unknown",
            claim=f"{tool} ended with status {run.status}{detail}",
            evidence=[],
            reason=f"tool_{run.status}",
        )
    return None


def scan(ctx: RuleContext, tool: str, query: str, count: int) -> ScanEvidence:
    """ScanEvidence for a tool/extractor query; the version comes from its run record."""
    run = ctx.tool_run(tool)
    return ScanEvidence(
        tool=tool, tool_version=run.version if run else __version__, query=query, result_count=count
    )


def fact_evidence(facts: Iterable[Fact], limit: int = MAX_EVIDENCE) -> list[Evidence]:
    """Evidence of `facts` in order, de-duplicated, at most `limit` items."""
    out: list[Evidence] = []
    for fact in facts:
        for item in fact.evidence:
            if item not in out:
                out.append(item)
            if len(out) >= limit:
                return out
    return out


def attr_str(fact: Fact, key: str) -> str | None:
    value = fact.attributes.get(key)
    return value if isinstance(value, str) else None


def attr_int(fact: Fact, key: str) -> int:
    value = fact.attributes.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def attr_bool(fact: Fact, key: str) -> bool:
    return fact.attributes.get(key) is True


def attr_list(fact: Fact, key: str) -> list[JsonValue]:
    value = fact.attributes.get(key)
    return value if isinstance(value, list) else []


def attr_dict(fact: Fact, key: str) -> dict[str, JsonValue]:
    value = fact.attributes.get(key)
    return value if isinstance(value, dict) else {}


def plural_noun(noun: str) -> str:
    if noun.endswith("y") and noun[-2:-1] not in ("a", "e", "o", "u"):
        return noun[:-1] + "ies"
    return noun + "s"


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {plural_noun(noun)}"


def max_severity_outcome(
    ctx: RuleContext,
    *,
    tool: str,
    findings: list[Fact],
    fail_at: Severity,
    partial_at: Severity | None,
    noun: str,
    describe: Callable[[Fact], str],
) -> RuleOutcome:
    """fail if any finding is at or above `fail_at`, partial if at or above `partial_at`, else pass.

    Findings without a severity never reach a threshold; they are only counted in the claim.
    """
    floor = fail_at
    if partial_at is not None and SEVERITY_RANK[partial_at] < SEVERITY_RANK[fail_at]:
        floor = partial_at
    over = sorted(
        (f for f in findings if severity_rank(f.severity) >= SEVERITY_RANK[floor]),
        key=lambda f: -severity_rank(f.severity),
    )
    if not over:
        rest = f" ({plural(len(findings), 'lower-severity finding')})" if findings else ""
        return RuleOutcome(
            verdict="pass",
            claim=f"No {plural_noun(noun)} at or above {floor} severity{rest}.",
            evidence=[scan(ctx, tool, f"{plural_noun(noun)} with severity >= {floor}", 0)],
        )
    worst = over[0].severity or "info"
    verdict = "fail" if SEVERITY_RANK[worst] >= SEVERITY_RANK[fail_at] else "partial"
    examples = ", ".join(describe(f) for f in over[:3])
    return RuleOutcome(
        verdict=verdict,
        claim=f"{plural(len(over), noun)} at or above {floor} severity "
        f"(worst: {worst}): {examples}.",
        evidence=fact_evidence(over),
    )
