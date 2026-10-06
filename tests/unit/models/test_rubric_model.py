from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from archlens.models import CheckSpec, Rubric

RUBRICS = Path(__file__).parents[3] / "rubrics"


@pytest.mark.parametrize("file_name", ["security.yaml", "_template.yaml"])
def test_shipped_rubric_files_parse(file_name: str) -> None:
    data = yaml.safe_load((RUBRICS / file_name).read_text(encoding="utf-8"))
    rubric = Rubric.model_validate(data)
    assert rubric.checks


def test_security_rubric_content() -> None:
    data = yaml.safe_load((RUBRICS / "security.yaml").read_text(encoding="utf-8"))
    rubric = Rubric.model_validate(data)
    assert [c.id for c in rubric.checks] == [f"SEC-0{i}" for i in range(1, 8)]
    by_id = {c.id: c for c in rubric.checks}
    assert by_id["SEC-05"].consistency_runs == 2  # explicit
    assert by_id["SEC-04"].consistency_runs == 1  # high → default 1
    assert by_id["SEC-06"].applies_when.any == ["has_http_api"]


def _det(**overrides: Any) -> dict[str, Any]:
    return {
        "id": "EX-01",
        "title": "t",
        "type": "deterministic",
        "severity": "medium",
        "rule": "files.any_exists",
        "rationale": "r",
        "remediation": "m",
    } | overrides


def _llm(**overrides: Any) -> dict[str, Any]:
    return {
        "id": "EX-02",
        "title": "t",
        "type": "llm",
        "severity": "critical",
        "guidance": "- pass: ...",
        "rationale": "r",
        "remediation": "m",
    } | overrides


def test_critical_llm_check_defaults_to_two_runs() -> None:
    assert CheckSpec.model_validate(_llm()).consistency_runs == 2


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (_det(rule=None), "deterministic check needs `rule`"),
        (_llm(guidance=None), "llm check needs `guidance`"),
        (_llm(evidence_policy="absence_allowed"), "requires `absence_probes`"),
        (_llm(na_allowed=True), "requires `absence_probes`"),
        (_det(id="ex-1"), "String should match pattern"),
        (_det(colour="red"), "Extra inputs are not permitted"),
    ],
)
def test_invalid_checks_are_rejected(data: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        CheckSpec.model_validate(data)


def test_fields_of_the_other_check_type_are_tolerated() -> None:
    det = CheckSpec.model_validate(_det(guidance="notes for humans", fact_kinds=["route"]))
    llm = CheckSpec.model_validate(_llm(params={"unused": True}))
    assert det.guidance == "notes for humans"
    assert llm.params == {"unused": True}


def test_duplicate_check_ids_are_rejected() -> None:
    data = {
        "metric": "example",
        "title": "Example",
        "version": "1.0.0",
        "weight": 1.0,
        "description": "d",
        "checks": [_det(), _det()],
    }
    with pytest.raises(ValidationError, match="duplicate check ids: \\['EX-01'\\]"):
        Rubric.model_validate(data)
