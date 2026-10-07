"""AssessmentReport builder, cost summary and config fingerprint."""

from archlens.models import AssessmentReport
from archlens.report import cost_summary, with_narrative
from tests.unit.report.helpers import NARRATIVE, SHA, records, sample_report


def test_findings_are_split_and_ordered() -> None:
    report = sample_report()
    assert [f.id for f in report.findings] == [
        f"{c}@{SHA[:12]}" for c in ("SEC-01", "SEC-05", "SEC-07", "TEST-01")
    ]
    assert [f.result.check_id for f in report.other_findings] == ["SEC-06", "TEST-05"]
    assert all(f.scored for f in report.findings) and not any(
        f.scored for f in report.other_findings
    )


def test_scores_come_from_the_scorer() -> None:
    report = sample_report()
    security, testing = report.metric_scores
    assert (security.score, security.capped_by, testing.score) == (4.0, ["SEC-05"], 10.0)
    assert report.overall_score is None  # fewer than 5 metrics


def test_cost_summary_is_exact_and_order_independent() -> None:
    summary = cost_summary(records())
    assert summary.usd == 0.0214 and summary.input_tokens == 10_000
    assert summary.by_stage == {"evaluate": 0.0194, "report": 0.0011, "verify": 0.0009}
    assert summary.by_metric == {"security": 0.0132, "testing": 0.0071}
    assert cost_summary(list(reversed(records()))) == summary


def test_with_narrative_updates_cost() -> None:
    report = sample_report()
    updated = with_narrative(report, NARRATIVE, records()[:2])
    assert updated.narrative == NARRATIVE and updated.cost.usd == 0.0194
    assert AssessmentReport.model_validate_json(updated.model_dump_json()) == updated


def test_config_fingerprint() -> None:
    config = sample_report().config
    assert config.rubric_versions == {"security": "1.0.0", "testing": "1.0.0"}
    assert config.models["synth"] == "synth-model"
    assert config.tool_versions == {"ast:ci": "0.1.0", "gitleaks": "8.28.0"}
