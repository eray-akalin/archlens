"""`AssessmentReport` builder (ARCHITECTURE.md §2.7): the canonical `assessment.json`.

Scores come from the scorer; findings are split into scored and other ones and ordered by
(metric, check id); the cost summary sums `Decimal`s of the call records in id order. Every
rendering (Markdown, HTML, SARIF, PR comment) is derived from the report alone.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal

from archlens import __version__
from archlens.config import ModelsConfig
from archlens.models import (
    SCHEMA_VERSION,
    AssessmentReport,
    ConfigFingerprint,
    CostSummary,
    Finding,
    LLMCallRecord,
    Narrative,
    RepoProfile,
    Rubric,
    ToolRunRecord,
)
from archlens.score import score_assessment

USD_PLACES = Decimal("0.000001")


def build_report(
    *,
    run_id: str,
    repo_url: str | None,
    commit_sha: str,
    created_at: datetime,
    config: ConfigFingerprint,
    profile: RepoProfile,
    rubrics: Mapping[str, Rubric],
    findings: Sequence[Finding],
    tool_runs: Sequence[ToolRunRecord],
    llm_records: Sequence[LLMCallRecord],
    timings_ms: Mapping[str, int],
    narrative: Narrative | None = None,
) -> AssessmentReport:
    """Score `findings` and assemble the report. Raises ValueError for two findings of a check."""
    scores = score_assessment(rubrics, findings, profile)
    ordered = sorted(findings, key=lambda f: (f.result.metric, f.result.check_id))
    return AssessmentReport(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        repo_url=repo_url,
        commit_sha=commit_sha,
        created_at=created_at,
        config=config,
        profile=profile,
        metric_scores=scores.metric_scores,
        overall_score=scores.overall,
        findings=[f for f in ordered if f.scored],
        other_findings=[f for f in ordered if not f.scored],
        tool_runs=list(tool_runs),
        cost=cost_summary(llm_records),
        timings_ms=dict(timings_ms),
        narrative=narrative,
    )


def with_narrative(
    report: AssessmentReport, narrative: Narrative | None, llm_records: Sequence[LLMCallRecord]
) -> AssessmentReport:
    """The report with its narrative and a cost summary that includes the synthesizer's calls."""
    return report.model_copy(update={"narrative": narrative, "cost": cost_summary(llm_records)})


def cost_summary(records: Sequence[LLMCallRecord]) -> CostSummary:
    """Token and USD totals, by stage and by metric (records without a metric count only in
    the totals and by stage)."""
    ordered = sorted(records, key=lambda r: r.id)
    by_stage: dict[str, Decimal] = {}
    by_metric: dict[str, Decimal] = {}
    for record in ordered:
        usd = Decimal(str(record.cost_usd))
        by_stage[record.stage] = by_stage.get(record.stage, Decimal(0)) + usd
        if record.metric is not None:
            by_metric[record.metric] = by_metric.get(record.metric, Decimal(0)) + usd
    return CostSummary(
        input_tokens=sum(r.input_tokens for r in ordered),
        cached_input_tokens=sum(r.cached_input_tokens for r in ordered),
        output_tokens=sum(r.output_tokens for r in ordered),
        reasoning_tokens=sum(r.reasoning_tokens for r in ordered),
        usd=_usd(sum(by_stage.values(), Decimal(0))),
        by_stage={k: _usd(v) for k, v in sorted(by_stage.items())},
        by_metric={k: _usd(v) for k, v in sorted(by_metric.items())},
    )


def config_fingerprint(
    *,
    rubrics: Mapping[str, Rubric],
    prompt_versions: Mapping[str, str],
    models: ModelsConfig,
    tool_runs: Sequence[ToolRunRecord],
) -> ConfigFingerprint:
    """What produced the report: versions of ArchLens, rubrics, prompts, models and tools."""
    roles = models.roles
    deployments = {
        name: getattr(roles, name).deployment
        for name in ("evaluator", "verifier", "skeptic", "synth", "embed")
    }
    return ConfigFingerprint(
        archlens_version=__version__,
        rubric_versions={name: rubrics[name].version for name in sorted(rubrics)},
        prompt_versions=dict(sorted(prompt_versions.items())),
        models=deployments,
        tool_versions={r.tool: r.version for r in sorted(tool_runs, key=lambda r: r.tool)},
    )


def _usd(value: Decimal) -> float:
    return float(value.quantize(USD_PLACES))
