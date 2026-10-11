"""OpenTelemetry (ARCHITECTURE.md §8) with in-memory exporters: one trace per run with stage and
metric spans, LLM call spans with token counters, and attributes that carry no repository text."""

from pathlib import Path

import pytest
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from archlens.llm.fake import FakeLLM
from tests.fixture_repos import MaterializedRepo
from tests.integration.test_assess_offline import evaluator_script, later_script, run
from tests.unit.llm.helpers import ScriptedProvider, answer, make_client
from tests.unit.llm.test_client import GOOD, request

pytestmark = pytest.mark.integration
ALLOWED_PREFIXES = ("archlens.", "gen_ai.")


@pytest.fixture(scope="module")
def otel() -> tuple[InMemorySpanExporter, InMemoryMetricReader]:
    """Global SDK providers (settable once per process; other tests leave them unset)."""
    exporter, reader = InMemorySpanExporter(), InMemoryMetricReader()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))
    return exporter, reader


def metric_points(reader: InMemoryMetricReader) -> dict[str, list[dict[str, object]]]:
    data = reader.get_metrics_data()
    out: dict[str, list[dict[str, object]]] = {}
    for resource in data.resource_metrics if data else []:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                out.setdefault(metric.name, []).extend(
                    {
                        **dict(p.attributes or {}),
                        "_value": getattr(p, "value", getattr(p, "sum", None)),
                    }
                    for p in metric.data.data_points
                )
    return out


async def test_one_trace_per_run(
    otel: tuple[InMemorySpanExporter, InMemoryMetricReader],
    tiny_service: MaterializedRepo,
    tmp_path: Path,
) -> None:
    exporter, reader = otel
    exporter.clear()
    await run(tiny_service, tmp_path, FakeLLM(evaluator_script() | later_script()), run_id="otel")
    spans: list[ReadableSpan] = list(exporter.get_finished_spans())
    (root,) = [s for s in spans if s.name == "archlens.run"]
    assert root.context is not None
    assert root.attributes is not None and root.attributes["archlens.run_id"] == "otel"
    assert {s.context.trace_id for s in spans if s.context} == {root.context.trace_id}
    stages = {s.name: s for s in spans if s.name.startswith("stage ")}
    assert set(stages) == {f"stage {n}" for n in (
        "ingest", "facts", "profile", "index", "evaluate", "verify", "score", "report"
    )}  # fmt: skip
    assert all(
        s.parent is not None and s.parent.span_id == root.context.span_id for s in stages.values()
    )
    metric_spans = [s for s in spans if s.name.startswith("metric ")]
    evaluate_context = stages["stage evaluate"].context
    assert evaluate_context is not None
    evaluate = evaluate_context.span_id
    assert len(metric_spans) == 10 and all(
        s.parent and s.parent.span_id == evaluate for s in metric_spans
    )
    for span in spans:  # ids, names and numbers only — never repository text
        assert all(k.startswith(ALLOWED_PREFIXES) for k in (span.attributes or {})), span.name
    points = metric_points(reader)
    verdicts = {(p["metric"], p["verdict"]) for p in points["archlens.check.verdicts"]}
    assert ("security", "fail") in verdicts
    assert "archlens.run.duration_s" in points


async def test_failed_run_marks_the_trace(
    otel: tuple[InMemorySpanExporter, InMemoryMetricReader], tmp_path: Path
) -> None:
    exporter, _ = otel
    exporter.clear()
    missing = MaterializedRepo(root=tmp_path / "nope", secret_values=())
    with pytest.raises(Exception):  # noqa: B017 (any ingest failure)
        await run(missing, tmp_path, FakeLLM(), run_id="broken")
    (root,) = [s for s in exporter.get_finished_spans() if s.name == "archlens.run"]
    assert root.status.status_code.name == "ERROR"
    (ingest,) = [s for s in exporter.get_finished_spans() if s.name == "stage ingest"]
    assert ingest.status.status_code.name == "ERROR"


async def test_llm_call_span_and_token_counters(
    otel: tuple[InMemorySpanExporter, InMemoryMetricReader],
) -> None:
    exporter, reader = otel
    exporter.clear()
    provider = ScriptedProvider(
        answer(GOOD.model_dump_json(), input_tokens=1000, cached=200, output=300, reasoning=100)
    )
    await make_client(provider).complete(request())
    (span,) = [s for s in exporter.get_finished_spans() if s.name.startswith("llm ")]
    attributes = span.attributes or {}
    assert attributes["gen_ai.usage.input_tokens"] == 1000
    assert attributes["archlens.reasoning_tokens"] == 100
    kinds = {
        p["kind"]: p["_value"] for p in metric_points(reader)["archlens.llm.tokens"]
        if p.get("stage") == span.attributes["archlens.stage"]  # type: ignore[index]
    }  # fmt: skip
    assert {"input", "cached", "output", "reasoning"} <= set(kinds)
