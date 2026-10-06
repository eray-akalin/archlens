import json
from pathlib import Path

from typer.testing import CliRunner

from archlens.cli import app
from archlens.models import SCHEMA_VERSION
from archlens.models.schemas import TOP_LEVEL_MODELS, export_schemas, find_drift, render_schemas

COMMITTED = Path(__file__).parents[3] / "schemas"


def test_committed_schemas_match_models() -> None:
    drift = find_drift(COMMITTED)
    assert drift == [], f"schemas/ is stale ({drift}); run `uv run archlens schema export`"


def test_render_is_deterministic_and_versioned() -> None:
    first, second = render_schemas(), render_schemas()
    assert first == second
    assert set(first) == {f"{name}.json" for name in TOP_LEVEL_MODELS}
    for text in first.values():
        assert json.loads(text)["x-schema-version"] == SCHEMA_VERSION


def test_drift_detects_changed_missing_and_unexpected(tmp_path: Path) -> None:
    export_schemas(tmp_path)
    assert find_drift(tmp_path) == []

    (tmp_path / "finding.json").write_text("{}\n")  # as if the model changed after export
    (tmp_path / "rubric.json").unlink()
    (tmp_path / "old_model.json").write_text("{}\n")

    assert find_drift(tmp_path) == [
        "changed: finding.json",
        "missing: rubric.json",
        "unexpected: old_model.json",
    ]


def test_missing_directory_reports_everything_missing(tmp_path: Path) -> None:
    drift = find_drift(tmp_path / "nope")
    assert len(drift) == len(TOP_LEVEL_MODELS)
    assert all(d.startswith("missing: ") for d in drift)


def test_cli_schema_export(tmp_path: Path) -> None:
    out = tmp_path / "schemas"
    result = CliRunner().invoke(app, ["schema", "export", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert f"wrote {len(TOP_LEVEL_MODELS)} schemas" in result.output
    assert find_drift(out) == []
