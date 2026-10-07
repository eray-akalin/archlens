"""`observability.*` rules over `import_edge` and `route` facts (LOG-05)."""

from pydantic import Field
from wcmatch import fnmatch

from archlens.models import Fact, RuleOutcome
from archlens.rubric.context import RuleContext
from archlens.rubric.registry import RuleParams, rule
from archlens.rubric.rules._common import MAX_EVIDENCE, attr_str, fact_evidence, scan
from archlens.rubric.rules.imports import matches_module

TELEMETRY_MODULES = [
    "opentelemetry", "azure.monitor", "applicationinsights", "prometheus_client",
    "@opentelemetry", "prom-client", "OpenTelemetry", "Microsoft.ApplicationInsights",
    "io.micrometer", "io.prometheus", "go.opentelemetry.io",
]  # fmt: skip
TOOLS = ("ast:imports", "ast:routes")


class PresentParams(RuleParams):
    modules: list[str] = Field(default_factory=lambda: list(TELEMETRY_MODULES))
    route_patterns: list[str] = Field(
        default_factory=lambda: ["/health*", "/ready*", "/live*", "/metrics"]
    )


def _route_matches(route: Fact, patterns: list[str]) -> bool:
    path = (attr_str(route, "path") or "").rstrip("/") or "/"
    tail = "/" + path.rsplit("/", 1)[-1]  # `/api/v1/health` counts as a health route
    return any(fnmatch.fnmatch(p, patterns) for p in (path, tail))


@rule("observability.present")
def present(ctx: RuleContext, params: PresentParams) -> RuleOutcome:
    """pass on a telemetry SDK import or a health/readiness/metrics route, else fail; unknown
    when neither extractor could run."""
    runs = [ctx.tool_run(t) for t in TOOLS]
    if all(r is None or r.status in ("error", "timeout") for r in runs):
        return RuleOutcome(
            verdict="unknown", claim="Imports and routes could not be extracted.",
            evidence=[], reason="tool_error",
        )  # fmt: skip
    imports = [
        e for e in ctx.facts.by_kind("import_edge")
        if any(matches_module(attr_str(e, "to_module") or "", m) for m in params.modules)
    ]  # fmt: skip
    routes = [r for r in ctx.facts.by_kind("route") if _route_matches(r, params.route_patterns)]
    found = imports + routes
    query = "telemetry imports or health/metrics routes"
    if not found:
        return RuleOutcome(
            verdict="fail",
            claim="No telemetry SDK import and no health, readiness or metrics route.",
            evidence=[scan(ctx, "ast:routes", query, 0)],
        )
    modules = sorted({attr_str(e, "to_module") or "?" for e in imports})
    parts = [f"telemetry {modules[0]}"] if modules else []
    parts += [f"route {attr_str(routes[0], 'path')}"] if routes else []
    return RuleOutcome(
        verdict="pass",
        claim=f"Observability present: {', '.join(parts)}.",
        evidence=[
            scan(ctx, "ast:routes", query, len(found)),
            *fact_evidence(found, MAX_EVIDENCE - 1),
        ],
    )
