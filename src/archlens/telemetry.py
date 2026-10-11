"""OpenTelemetry for runs (docs/ARCHITECTURE.md §8).

One trace per run: a root span `archlens.run`, a span per stage, per metric session and per LLM
call. Metrics: `archlens.llm.tokens{kind, stage, metric, model}`, `archlens.llm.cost_usd{stage,
metric}`, `archlens.check.verdicts{metric, verdict}`, `archlens.verify.status{step, status}`,
`archlens.run.duration_s`. Instruments go through the OTel API, so they are no-ops until
`setup()` installs the Azure Monitor exporter (`ARCHLENS_OTEL_ENABLED=true` plus
`APPLICATIONINSIGHTS_CONNECTION_STRING`). Attributes never carry repository content, prompts or
secrets — only ids, names and numbers.
"""

import logging
import time

from opentelemetry import metrics, trace
from opentelemetry.trace import Span, Status, StatusCode, Tracer

from archlens import __version__
from archlens.config import Settings
from archlens.models import AssessmentReport, LLMCallRecord

logger = logging.getLogger(__name__)
NAME = "archlens"

_meter = metrics.get_meter(NAME, __version__)
_tokens = _meter.create_counter("archlens.llm.tokens", unit="{token}")
_cost = _meter.create_counter("archlens.llm.cost_usd", unit="USD")
_verdicts = _meter.create_counter("archlens.check.verdicts", unit="{check}")
_verify = _meter.create_counter("archlens.verify.status", unit="{finding}")
_duration = _meter.create_histogram("archlens.run.duration_s", unit="s")
_configured = False


def tracer() -> Tracer:
    return trace.get_tracer(NAME, __version__)


def setup(settings: Settings) -> bool:
    """Install the Azure Monitor exporter once if telemetry is enabled; True when exporting."""
    global _configured
    secret = settings.applicationinsights_connection_string
    if _configured or not settings.otel_enabled or secret is None:
        return _configured
    from azure.monitor.opentelemetry import (  # heavy; load only if used
        configure_azure_monitor,  # pyright: ignore[reportUnknownVariableType]
    )

    configure_azure_monitor(
        connection_string=secret.get_secret_value(),
        logger_name=NAME,
        instrumentation_options={"fastapi": {"enabled": True}},
        # Keep every span. Without a sampler setting the distro (1.8) installs a per-span
        # RateLimitedSampler at 5 spans/s, which split run traces and dropped their root span.
        sampling_ratio=1.0,
    )
    _configured = True
    logger.info("OpenTelemetry exporting to Application Insights")
    return True


def shutdown() -> None:
    """Flush pending spans and metrics (a worker job exits right after its run)."""
    for provider in (trace.get_tracer_provider(), metrics.get_meter_provider()):
        flush = getattr(provider, "force_flush", None)
        if callable(flush):
            flush()


def record_llm_call(record: LLMCallRecord) -> None:
    """A span covering the call (ended now, started `latency_ms` ago) and the token/cost
    counters."""
    end = time.time_ns()
    attributes = {
        "archlens.stage": record.stage,
        "archlens.metric": record.metric or "",
        "gen_ai.request.model": record.model,
        "archlens.prompt_version": record.prompt_version,
        "gen_ai.usage.input_tokens": record.input_tokens,
        "gen_ai.usage.output_tokens": record.output_tokens,
        "archlens.cached_input_tokens": record.cached_input_tokens,
        "archlens.reasoning_tokens": record.reasoning_tokens,
        "archlens.cost_usd": record.cost_usd,
        "archlens.cache_hit": record.cache_hit,
        "archlens.attempt": record.attempt,
    }
    span = tracer().start_span(
        f"llm {record.stage}", start_time=end - record.latency_ms * 1_000_000, attributes=attributes
    )
    span.end(end_time=end)
    labels = {"stage": record.stage, "metric": record.metric or "", "model": record.model}
    for kind, count in (
        ("input", record.input_tokens - record.cached_input_tokens),
        ("cached", record.cached_input_tokens),
        ("output", record.output_tokens - record.reasoning_tokens),
        ("reasoning", record.reasoning_tokens),
    ):
        if count > 0:
            _tokens.add(count, {**labels, "kind": kind})
    _cost.add(record.cost_usd, {"stage": record.stage, "metric": record.metric or ""})


def record_report(report: AssessmentReport, duration_s: float) -> None:
    """Verdict and verification counters for a finished run, and its duration."""
    for finding in [*report.findings, *report.other_findings]:
        _verdicts.add(1, {"metric": finding.result.metric, "verdict": finding.result.verdict})
        _verify.add(1, {"step": "final", "status": finding.verification.status})
        for step in finding.verification.steps:
            _verify.add(1, {"step": step.step, "status": "passed" if step.passed else "failed"})
    _duration.record(duration_s)


def fail(span: Span, exc: BaseException) -> None:
    span.record_exception(exc)
    span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
