"""Rubric loader rejections, and the shipped rubrics against the RUBRICS.md §3 catalogue."""

import copy
import logging
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from archlens.errors import RubricError
from archlens.models import Rubric
from archlens.profile import FLAGS
from archlens.rubric import load_rubric, load_rubrics

REPO = Path(__file__).parents[3]
RUBRICS_DIR = REPO / "rubrics"
RUBRICS_DOC = REPO / "docs" / "RUBRICS.md"

VALID: dict[str, Any] = {
    "metric": "example",
    "title": "Example",
    "version": "1.0.0",
    "weight": 1.0,
    "description": "d",
    "checks": [
        {
            "id": "EX-01",
            "title": "deterministic",
            "type": "deterministic",
            "severity": "low",
            "rationale": "r",
            "remediation": "r",
            "rule": "files.any_exists",
            "params": {"globs": ["README*"]},
        },
        {
            "id": "EX-02",
            "title": "llm",
            "type": "llm",
            "severity": "high",
            "rationale": "r",
            "remediation": "r",
            "guidance": "- pass: ...",
            "evidence_policy": "absence_allowed",
            "absence_probes": [{"kind": "path_glob", "pattern": "README*"}],
        },
    ],
}


def write(tmp_path: Path, data: object, name: str = "example.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data) if not isinstance(data, str) else data)
    return path


def variant(**changes: Any) -> dict[str, Any]:
    """VALID with dotted-path changes, e.g. `**{"checks.0.rule": "x"}`; None deletes the key."""
    data = copy.deepcopy(VALID)
    for dotted, value in changes.items():
        *parents, last = dotted.split(".")
        node: Any = data
        for part in parents:
            node = node[int(part)] if part.isdigit() else node[part]
        if value is None:
            del node[last]
        else:
            node[last] = value
    return data


def test_valid_rubric_loads(tmp_path: Path) -> None:
    rubric = load_rubric(write(tmp_path, VALID))
    assert [c.id for c in rubric.checks] == ["EX-01", "EX-02"]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"owner": "me"}, "owner"),  # unknown top-level key
        ({"checks.0.severty": "low"}, "severty"),  # unknown check key
        ({"checks.0.rule": "files.nope"}, "unknown rule 'files.nope'"),
        ({"checks.0.params": {"globs": ["x"], "glob": ["y"]}}, "params"),
        ({"checks.0.params": {}}, "params"),  # required param missing
        ({"checks.0.rule": None}, "needs `rule`"),
        ({"checks.1.absence_probes": []}, "absence_probes"),  # absence_allowed without probes
        (
            {"checks.1.evidence_policy": "positive_required", "checks.1.na_allowed": True,
             "checks.1.absence_probes": []},
            "absence_probes",
        ),  # na_allowed without probes
        ({"metric": "other"}, "file name stem"),
        ({"version": "1.0"}, "version"),
        ({"checks.1.id": "EX-01"}, "duplicate"),
    ],
)  # fmt: skip
def test_invalid_rubrics_are_rejected(
    changes: dict[str, Any], message: str, tmp_path: Path
) -> None:
    with pytest.raises(RubricError, match=re.escape(message)):
        load_rubric(write(tmp_path, variant(**changes)))


def test_invalid_yaml_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(RubricError, match=r"example\.yaml"):
        load_rubric(write(tmp_path, "metric: [unclosed\n"))


def test_retired_checks_skip_rule_validation(tmp_path: Path) -> None:
    rubric = load_rubric(
        write(tmp_path, variant(**{"checks.0.rule": "gone.away", "checks.0.retired": True}))
    )
    assert rubric.checks[0].retired


def test_unknown_flags_are_logged_not_rejected(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="archlens.rubric.loader"):
        load_rubric(write(tmp_path, variant(**{"checks.0.applies_when": {"any": ["has_cii"]}})))
    assert "has_cii" in caplog.text


def test_load_rubrics_skips_underscore_files(tmp_path: Path) -> None:
    write(tmp_path, VALID)
    write(tmp_path, {"anything": "goes"}, name="_template.yaml")
    assert list(load_rubrics(tmp_path)) == ["example"]


# --- shipped rubrics ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def shipped() -> dict[str, Rubric]:
    return load_rubrics(RUBRICS_DIR)


def test_shipped_rubrics_load(shipped: dict[str, Rubric]) -> None:
    assert {"security", "testing", "cicd"} <= set(shipped)


def test_template_is_a_valid_rubric_shape() -> None:
    data = yaml.safe_load((RUBRICS_DIR / "_template.yaml").read_text())
    Rubric.model_validate(data)


def test_shipped_rubrics_only_use_known_flags(shipped: dict[str, Rubric]) -> None:
    for rubric in shipped.values():
        for cond in [rubric.applies_when, *(c.applies_when for c in rubric.checks)]:
            assert set(cond.all + cond.any + cond.none) <= set(FLAGS), rubric.metric


def _catalogue() -> dict[str, tuple[float, dict[str, Any], dict[str, dict[str, Any]]]]:
    """metric → (weight, metric applies_when, {check id → {type, severity, rule, applies_when}})."""
    out: dict[str, tuple[float, dict[str, Any], dict[str, dict[str, Any]]]] = {}
    severities = {"C": "critical", "H": "high", "M": "medium", "L": "low"}
    current: dict[str, dict[str, Any]] = {}
    for line in RUBRICS_DOC.read_text().splitlines():
        if heading := re.match(
            r"^### (\w+) — .*\(weight ([\d.]+)(?:, `applies_when: (.+?)`)?\)", line
        ):
            current = {}
            applies = yaml.safe_load(heading.group(3)) if heading.group(3) else {}
            out[heading.group(1)] = (float(heading.group(2)), applies, current)
        elif row := re.match(r"^\| ([A-Z]+-\d{2}) \|", line):
            cells = [c.strip() for c in line.strip("|").split("|")]
            notes = cells[4]
            rule = re.match(r"`([a-z_]+\.[a-z_]+)`", notes)
            applies = re.search(r"`applies_when: (\{.+?\})`", notes)
            current[row.group(1)] = {
                "type": {"D": "deterministic", "L": "llm"}[cells[2]],
                "severity": severities[cells[3]],
                "rule": rule.group(1) if rule else None,
                "applies_when": yaml.safe_load(applies.group(1)) if applies else {},
            }
    return out


def test_shipped_rubrics_match_the_catalogue(shipped: dict[str, Rubric]) -> None:
    catalogue = _catalogue()
    for metric, rubric in shipped.items():
        weight, applies, checks = catalogue[metric]
        assert rubric.weight == weight, metric
        assert rubric.applies_when.model_dump(exclude_defaults=True) == applies, metric
        assert [c.id for c in rubric.checks if not c.retired] == list(checks), metric
        for spec in rubric.checks:
            row = checks[spec.id]
            assert spec.type == row["type"], spec.id
            assert spec.severity == row["severity"], spec.id
            if spec.type == "deterministic":
                assert spec.rule == row["rule"], spec.id
            assert spec.applies_when.model_dump(exclude_defaults=True) == row["applies_when"], (
                spec.id
            )
