from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from archlens.models import (
    CodeEvidence,
    Evidence,
    FactSet,
    Finding,
    MetricScore,
    ScanEvidence,
)
from archlens.models.schemas import TOP_LEVEL_MODELS
from tests.unit.models import samples


def test_every_top_level_model_has_a_sample() -> None:
    assert set(samples.SAMPLES) == set(TOP_LEVEL_MODELS)
    for name, sample in samples.SAMPLES.items():
        assert type(sample) is TOP_LEVEL_MODELS[name]


@pytest.mark.parametrize("name", sorted(TOP_LEVEL_MODELS))
def test_json_round_trip(name: str) -> None:
    sample = samples.SAMPLES[name]
    model = TOP_LEVEL_MODELS[name]
    restored = model.model_validate_json(sample.model_dump_json())
    assert restored == sample


def test_contracts_reject_unknown_fields() -> None:
    data = samples.code_evidence().model_dump() | {"confidence": "high"}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CodeEvidence.model_validate(data)


def test_contracts_are_frozen() -> None:
    evidence = samples.code_evidence()
    with pytest.raises(ValidationError, match="frozen"):
        evidence.path = "elsewhere.py"  # pyright: ignore[reportAttributeAccessIssue]


def test_run_state_is_mutable_and_validated() -> None:
    state = samples.run_state()
    state.status = "done"
    assert state.status == "done"
    with pytest.raises(ValidationError):
        state.status = "exploded"  # pyright: ignore[reportAttributeAccessIssue]


def test_evidence_union_discriminates_on_kind() -> None:
    adapter: TypeAdapter[Evidence] = TypeAdapter(Evidence)
    assert isinstance(adapter.validate_python(samples.scan_evidence().model_dump()), ScanEvidence)
    assert isinstance(adapter.validate_python(samples.code_evidence().model_dump()), CodeEvidence)


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (0, 1, "greater than or equal to 1"),
        (10, 9, "end_line must be >= start_line"),
        (1, 61, "span must be < 60"),
    ],
)
def test_code_evidence_line_rules(start: int, end: int, message: str) -> None:
    data = samples.code_evidence().model_dump() | {"start_line": start, "end_line": end}
    with pytest.raises(ValidationError, match=message):
        CodeEvidence.model_validate(data)


def test_code_evidence_span_of_60_lines_is_allowed() -> None:
    data = samples.code_evidence().model_dump() | {"start_line": 1, "end_line": 60}
    assert CodeEvidence.model_validate(data).end_line == 60


def test_finding_scored_must_match_verification() -> None:
    data: dict[str, Any] = samples.rejected_finding().model_dump() | {"scored": True}
    with pytest.raises(ValidationError, match="scored must be True iff"):
        Finding.model_validate(data)


def test_verified_unknown_is_not_scored() -> None:
    data: dict[str, Any] = samples.verified_finding().model_dump()
    data["result"]["verdict"] = "unknown"
    with pytest.raises(ValidationError, match="scored must be True iff"):
        Finding.model_validate(data)
    data["scored"] = False
    assert Finding.model_validate(data).scored is False


@pytest.mark.parametrize(("status", "score"), [("scored", None), ("insufficient_evidence", 5.0)])
def test_metric_score_set_iff_scored(status: str, score: float | None) -> None:
    with pytest.raises(ValidationError, match="score must be set iff"):
        MetricScore(
            metric="security",
            status=status,  # pyright: ignore[reportArgumentType]
            score=score,
            coverage=0.5,
            capped_by=[],
            counts={},
        )


def test_report_sections_hold_the_right_findings() -> None:
    report = samples.assessment_report()
    swapped = report.model_dump() | {
        "findings": report.model_dump()["other_findings"],
        "other_findings": report.model_dump()["findings"],
    }
    with pytest.raises(ValidationError, match="`findings` may only hold scored findings"):
        type(report).model_validate(swapped)


def test_fact_set_by_kind() -> None:
    facts = samples.fact_set()
    assert [f.kind for f in facts.by_kind("route", "secret")] == ["route"]
    assert facts.by_kind("secret") == []
    assert FactSet(commit_sha="x", facts=[], tool_runs=[]).by_kind("route") == []


def _walk(node: object) -> list[dict[str, Any]]:
    """Every dict node of a JSON Schema."""
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        found.append(node)  # pyright: ignore[reportUnknownArgumentType]
        for value in node.values():  # pyright: ignore[reportUnknownVariableType]
            found.extend(_walk(value))
    elif isinstance(node, list):
        for value in node:  # pyright: ignore[reportUnknownVariableType]
            found.extend(_walk(value))
    return found


LLM_FACING = ["metric_evaluation_output", "entailment_batch_output", "skeptic_output", "narrative"]


@pytest.mark.parametrize("name", LLM_FACING)
def test_llm_facing_schemas_fit_strict_structured_outputs(name: str) -> None:
    model: type[BaseModel] = TOP_LEVEL_MODELS[name]
    nodes = _walk(model.model_json_schema())
    assert not [n for n in nodes if {"anyOf", "oneOf", "allOf"} & n.keys()], "no unions"
    objects = [n for n in nodes if n.get("type") == "object"]
    assert objects
    for obj in objects:
        assert "properties" in obj, "no free-form dicts"
        assert obj.get("additionalProperties") is False
        assert set(obj.get("required", [])) == set(obj["properties"]), "every field required"
