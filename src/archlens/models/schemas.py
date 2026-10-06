"""JSON Schema export for the top-level contracts, and drift detection against `schemas/`."""

import json
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel

from archlens.models.base import SCHEMA_VERSION
from archlens.models.facts import FactSet
from archlens.models.llm_output import EntailmentBatchOutput, MetricEvaluationOutput, SkepticOutput
from archlens.models.report import AssessmentReport, LLMCallRecord, Narrative
from archlens.models.results import CheckResult, Finding
from archlens.models.rubric import Rubric
from archlens.models.run_state import RunState
from archlens.models.snapshot import RepoProfile, RepoSnapshot

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

# Models that are persisted, exchanged between stages, or returned by an LLM.
TOP_LEVEL_MODELS: Mapping[str, type[BaseModel]] = {
    "assessment_report": AssessmentReport,
    "repo_snapshot": RepoSnapshot,
    "repo_profile": RepoProfile,
    "fact_set": FactSet,
    "check_result": CheckResult,
    "finding": Finding,
    "run_state": RunState,
    "llm_call_record": LLMCallRecord,
    "rubric": Rubric,
    "metric_evaluation_output": MetricEvaluationOutput,
    "entailment_batch_output": EntailmentBatchOutput,
    "skeptic_output": SkepticOutput,
    "narrative": Narrative,
}


def render_schemas() -> dict[str, str]:
    """File name → JSON text for every top-level model. Deterministic for equal models."""
    rendered: dict[str, str] = {}
    for name, model in TOP_LEVEL_MODELS.items():
        document = {
            "$schema": JSON_SCHEMA_DIALECT,
            "x-schema-version": SCHEMA_VERSION,
            **model.model_json_schema(),
        }
        rendered[f"{name}.json"] = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    return rendered


def export_schemas(out_dir: Path) -> list[Path]:
    """Write all schemas to `out_dir` (created if needed); returns the written paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for file_name, text in render_schemas().items():
        path = out_dir / file_name
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def find_drift(schema_dir: Path) -> list[str]:
    """Differences between the models and `schema_dir`, e.g. `changed: finding.json`.

    Empty when the directory matches a fresh export exactly. A missing directory reports every
    schema as missing.
    """
    expected = render_schemas()
    on_disk = {p.name: p for p in schema_dir.glob("*.json")} if schema_dir.is_dir() else {}
    drift: list[str] = []
    for file_name, text in expected.items():
        path = on_disk.get(file_name)
        if path is None:
            drift.append(f"missing: {file_name}")
        elif path.read_text(encoding="utf-8") != text:
            drift.append(f"changed: {file_name}")
    drift.extend(f"unexpected: {name}" for name in sorted(on_disk.keys() - expected.keys()))
    return drift
