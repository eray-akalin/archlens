"""`imports.*` rules over `import_edge` facts (STR-04, LOG-01)."""

from collections.abc import Iterator

from pydantic import Field

from archlens.models import Fact, RuleOutcome
from archlens.rubric.context import RuleContext
from archlens.rubric.registry import RuleParams, rule
from archlens.rubric.rules._common import (
    MAX_EVIDENCE,
    attr_bool,
    attr_str,
    fact_evidence,
    missing_data,
    scan,
)

TOOL = "ast:imports"
CYCLE_LANGUAGES = ("lang_python", "lang_js_ts")
LOGGING_MODULES = [
    # Python
    "logging", "structlog", "loguru",
    # JS/TS
    "winston", "pino", "bunyan", "log4js",
    # .NET
    "Microsoft.Extensions.Logging", "Serilog", "NLog",
    # Java
    "org.slf4j", "org.apache.logging.log4j", "java.util.logging",
    # Go
    "log/slog", "go.uber.org/zap", "github.com/rs/zerolog", "github.com/sirupsen/logrus",
]  # fmt: skip


class NoCyclesParams(RuleParams):
    scope: str = "internal"


def _node(module: str) -> str:
    """JS `src/x/index` and an import of `src/x` are the same module."""
    return module.removesuffix("/index")


def strongly_connected(graph: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan's algorithm (iterative); components in discovery order, members sorted."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    result: list[list[str]] = []
    counter = 0
    for root in sorted(graph):
        if root in index:
            continue
        work: list[tuple[str, Iterator[str]]] = [(root, iter(sorted(graph[root])))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            child = next(children, None)
            if child is None:
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    component: list[str] = []
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        component.append(member)
                        if member == node:
                            break
                    result.append(sorted(component))
            elif child not in index:
                index[child] = low[child] = counter
                counter += 1
                stack.append(child)
                on_stack.add(child)
                work.append((child, iter(sorted(graph.get(child, set())))))
            elif child in on_stack:
                low[node] = min(low[node], index[child])
    return result


@rule("imports.no_cycles")
def no_cycles(ctx: RuleContext, params: NoCyclesParams) -> RuleOutcome:
    """Cycles among internal modules (Tarjan SCC); NA outside Python/JS/TS."""
    if (missing := missing_data(ctx, TOOL)) is not None:
        return missing
    if not any(ctx.profile.flags.get(flag, False) for flag in CYCLE_LANGUAGES):
        return RuleOutcome(
            verdict="not_applicable", claim="Import cycles are only analysed for Python and JS/TS.",
            evidence=[], reason="language_not_supported",
        )  # fmt: skip
    edges = [e for e in ctx.facts.by_kind("import_edge") if attr_bool(e, "internal")]
    graph: dict[str, set[str]] = {}
    by_pair: dict[tuple[str, str], Fact] = {}
    for edge in edges:
        a, b = _node(attr_str(edge, "from_module") or ""), _node(attr_str(edge, "to_module") or "")
        if a and b and a != b:
            graph.setdefault(a, set()).add(b)
            graph.setdefault(b, set())
            by_pair.setdefault((a, b), edge)
    cycles = [c for c in strongly_connected(graph) if len(c) > 1]
    query = "cycles between internal modules"
    if not cycles:
        return RuleOutcome(
            verdict="pass",
            claim=f"No import cycles among {len(graph)} internal modules.",
            evidence=[scan(ctx, TOOL, query, 0)],
        )
    involved = [by_pair[(a, b)] for c in cycles for a in c for b in c if (a, b) in by_pair]
    shown = "; ".join(" ↔ ".join(c[:4]) for c in cycles[:3])
    return RuleOutcome(
        verdict="fail",
        claim=f"{len(cycles)} import cycle(s) among internal modules: {shown}.",
        evidence=[scan(ctx, TOOL, query, len(cycles)), *fact_evidence(involved, MAX_EVIDENCE - 1)],
    )


class AnyOfParams(RuleParams):
    modules: list[str] = Field(default_factory=lambda: list(LOGGING_MODULES), min_length=1)


def matches_module(module: str, wanted: str) -> bool:
    """Exact module, or a submodule/subpath of it (`logging.handlers`, `pino/file`)."""
    return module == wanted or module.startswith((f"{wanted}.", f"{wanted}/"))


@rule("imports.any_of")
def any_of(ctx: RuleContext, params: AnyOfParams) -> RuleOutcome:
    """pass if any import targets one of `modules` (prefix match), else fail; NA when no imports
    were extracted (a language the extractor doesn't read)."""
    if (missing := missing_data(ctx, TOOL)) is not None:
        return missing
    edges = ctx.facts.by_kind("import_edge")
    if not edges:
        return RuleOutcome(
            verdict="not_applicable", claim="No imports were extracted for this repository.",
            evidence=[scan(ctx, TOOL, "imports", 0)], reason="no_imports",
        )  # fmt: skip
    hits = [
        e for e in edges
        if any(matches_module(attr_str(e, "to_module") or "", m) for m in params.modules)
    ]  # fmt: skip
    query = (
        f"imports of {', '.join(params.modules[:6])}{', ...' if len(params.modules) > 6 else ''}"
    )
    if not hits:
        return RuleOutcome(
            verdict="fail",
            claim=f"None of the {len(edges)} imports uses one of: {', '.join(params.modules[:8])}.",
            evidence=[scan(ctx, TOOL, query, 0)],
        )
    names = sorted({attr_str(e, "to_module") or "?" for e in hits})
    return RuleOutcome(
        verdict="pass",
        claim=f"Imported: {', '.join(names[:4])}.",
        evidence=[scan(ctx, TOOL, query, len(hits)), *fact_evidence(hits, MAX_EVIDENCE - 1)],
    )
