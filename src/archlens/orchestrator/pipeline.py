"""The assessment pipeline: ingest → facts → profile → index → evaluate → verify → score → report.

A fixed sequence of stages (the LLM never decides control flow, ADR-001). Every stage output is
checkpointed; on `--resume` a stage with a checkpoint is skipped, and evaluate skips each metric
that has one. The repository is re-read on resume (re-cloned at the recorded commit for remote
targets) and must match the checkpointed snapshot. A stage-level exception marks the run and the
stage `failed`, keeps every checkpoint, and propagates; check-level problems never get here (they
are `unknown` verdicts). LLM call records are checkpointed too, so a resumed run's cost summary
covers every call.
"""

import asyncio
import logging
import tempfile
import time
from collections.abc import Awaitable, Callable
from contextvars import Token
from pathlib import Path

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import Span
from pydantic import BaseModel

from archlens import telemetry
from archlens.errors import ArchLensError, IngestError
from archlens.evaluate.consistency import evaluate_llm_checks
from archlens.evaluate.deterministic import llm_checks, run_deterministic_checks
from archlens.evaluate.llm_session import Evaluator, EvaluatorPrompts
from archlens.evaluate.result_cache import (
    FILES_NAMESPACE,
    FILES_TTL_S,
    NAMESPACE,
    CachedMetric,
    FileHashes,
    metric_inputs_key,
)
from archlens.evidence import SnippetReader
from archlens.facts.runner import collect_facts
from archlens.facts.scanners.gitleaks import spans_from_facts
from archlens.index import build_index
from archlens.ingest import Ingested, ingest, is_remote
from archlens.llm.prompts import load_prompts
from archlens.models import (
    AssessmentReport,
    CheckResult,
    FactSet,
    Finding,
    IngestLimits,
    LLMCallRecord,
    RepoProfile,
    RepoSnapshot,
    Rubric,
    RunState,
    StageState,
)
from archlens.orchestrator.checkpoint import (
    Checkpoints,
    FindingsCheckpoint,
    IndexCheckpoint,
    MetricCheckpoint,
    ScoresCheckpoint,
)
from archlens.orchestrator.context import RunContext, RunOptions
from archlens.profile import build_profile
from archlens.report import (
    SynthPrompts,
    build_report,
    config_fingerprint,
    render_html,
    render_markdown,
    render_pr_comment,
    render_sarif,
    synthesize,
    with_narrative,
)
from archlens.rubric import RuleContext
from archlens.score import score_assessment
from archlens.security.redact import Redactor, redact
from archlens.tools.repo_tools import RepoTools
from archlens.tools.seen import SeenLines
from archlens.verify.entailment import EntailmentPrompts
from archlens.verify.pipeline import Verifier
from archlens.verify.skeptic import SkepticPrompts

logger = logging.getLogger(__name__)

STAGES = ("ingest", "facts", "profile", "index", "evaluate", "verify", "score", "report")
ARTIFACTS = ("assessment.json", "report.md", "report.html", "findings.sarif", "pr_comment.md")


def queued_state(run_id: str, repo_url: str | None, ref: str | None) -> RunState:
    """A run's state before it starts: queued, every stage pending."""
    return RunState(
        run_id=run_id,
        repo_url=repo_url,
        ref=ref,
        status="queued",
        stages=[
            StageState(stage=s, status="pending", started_at=None, finished_at=None, error=None)
            for s in STAGES
        ],
        metrics_done=[],
        cost_usd=0.0,
    )


class Pipeline:
    def __init__(self, ctx: RunContext, options: RunOptions) -> None:
        self.ctx = ctx
        self.options = options
        self.cp = Checkpoints(ctx.storage.checkpoints, ctx.run_id)
        self.rubrics = _select(ctx.rubrics, options.metrics)
        self.ledger = SeenLines()
        self.timings: dict[str, int] = {}
        self.previous_records: list[LLMCallRecord] = []
        self.reused: list[str] = []  # metrics whose LLM results came from the metric cache
        self.state: RunState | None = None
        self._spans: dict[str, tuple[Span, Token[Context]]] = {}  # open stage spans + tokens

    async def run(self, *, resume: bool = False) -> AssessmentReport:
        """Run (or resume) the assessment; returns the report also stored as artifacts. One
        OpenTelemetry trace per run (root span `archlens.run`)."""
        target = self.options.target
        attributes = {
            "archlens.run_id": self.ctx.run_id,
            "archlens.repo": target if is_remote(target) else "local",
            "archlens.resume": resume,
        }
        started = time.monotonic()
        tracer = telemetry.tracer()
        with tracer.start_as_current_span("archlens.run", attributes=attributes) as root:
            await self._start(resume)
            try:
                with tempfile.TemporaryDirectory(prefix="archlens-") as tmp:
                    report = await self._stages(Path(tmp))
            except BaseException as exc:
                await self._fail(exc)
                telemetry.fail(root, exc)
                raise
            state = self._state()
            state.status = "done"
            await self._save_state()
            telemetry.record_report(report, time.monotonic() - started)
        return report

    # --- stages ---

    async def _stages(self, tmp: Path) -> AssessmentReport:
        ingested, snapshot = await self._ingest(tmp)
        root = ingested.root
        if self.options.metric_cache:  # PR mode: a later run with --base diffs against this
            hashes = FileHashes.of(snapshot.files).model_dump_json().encode()
            await self.ctx.storage.cache.put(
                FILES_NAMESPACE, snapshot.commit_sha, hashes, ttl_s=FILES_TTL_S
            )
        facts = await self._cached(
            "facts", FactSet, lambda: asyncio.to_thread(self._collect_facts, ingested, tmp)
        )
        redactor = Redactor(spans_from_facts(facts.facts))
        reader = SnippetReader(root, redactor)
        profile = await self._cached(
            "profile",
            RepoProfile,
            lambda: asyncio.to_thread(build_profile, snapshot, facts, reader),
        )
        index_path = self.ctx.work_dir / "index.sqlite"
        await self._index(index_path, snapshot, reader)
        tools = RepoTools(
            root,
            snapshot.files,
            facts,
            boundary=self.ctx.boundary,
            ledger=self.ledger,
            redactor=redactor,
            index_path=index_path,
            embedder=self.ctx.embedder,
        )
        results = await self._evaluate(
            tools, RuleContext.create(root, facts, profile, snapshot.files, redactor)
        )
        findings = await self._cached(
            "verify", FindingsCheckpoint, lambda: self._verify(tools, snapshot, results)
        )
        scores = await self._cached(
            "score", ScoresCheckpoint, lambda: self._score(findings.findings, profile)
        )
        logger.info("scores: %s", [(m.metric, m.score) for m in scores.metric_scores])
        report = await self._cached(
            "report",
            AssessmentReport,
            lambda: self._report(snapshot, facts, profile, findings.findings, tools.boundary),
        )
        await self._artifacts(report, snapshot)
        return report

    async def _ingest(self, tmp: Path) -> tuple[Ingested, RepoSnapshot]:
        recorded = await self.cp.load("ingest", RepoSnapshot)
        target = self.options.target
        ref = (
            recorded.commit_sha if recorded is not None and is_remote(target) else self.options.ref
        )
        started = await self._begin("ingest") if recorded is None else None
        ingested = await asyncio.to_thread(
            ingest, target, workdir=tmp / "clone", ref=ref, limits=IngestLimits()
        )
        if recorded is None:
            await self.cp.save("ingest", ingested.snapshot)
            await self._finish("ingest", started)
            return ingested, ingested.snapshot
        if recorded.commit_sha != ingested.snapshot.commit_sha:
            raise IngestError(
                f"the repository changed since run {self.ctx.run_id} started "
                f"({recorded.commit_sha[:12]} → {ingested.snapshot.commit_sha[:12]})"
            )
        await self._finish("ingest", None)
        return ingested, recorded

    def _collect_facts(self, ingested: Ingested, tmp: Path) -> FactSet:
        return collect_facts(
            ingested.root,
            ingested.snapshot,
            workdir=tmp / "tools",
            tools=self.ctx.config.tools,
            tools_dir=self.ctx.tools_dir,
            scanners=self.options.scanners,
        ).facts

    async def _index(self, path: Path, snapshot: RepoSnapshot, reader: SnippetReader) -> None:
        recorded = await self.cp.load("index", IndexCheckpoint)
        if recorded is not None and await asyncio.to_thread(path.exists):
            await self._finish("index", None)
            return
        started = await self._begin("index")
        stats = await build_index(path, snapshot, reader, self.ctx.embedder)
        await self.cp.save(
            "index", IndexCheckpoint(files=stats.files, chunks=stats.chunks, symbols=stats.symbols)
        )
        await self._finish("index", started)

    async def _evaluate(self, tools: RepoTools, rule_ctx: RuleContext) -> list[CheckResult]:
        flags = rule_ctx.profile.flags
        metrics = [r for r in self.rubrics.values() if r.applies_when.holds(flags)]
        limits = self.ctx.config.models.sessions.evaluator
        prompts = EvaluatorPrompts.load(self.ctx.prompts_dir)
        evaluator = Evaluator(
            self.ctx.llm, tools, rule_ctx.profile, rule_ctx.facts, limits, prompts
        )  # fmt: skip
        started = await self._begin("evaluate")
        by_metric: dict[str, list[CheckResult]] = {}

        async def one(rubric: Rubric) -> None:
            key = f"evaluate/{rubric.metric}"
            recorded = await self.cp.load(key, MetricCheckpoint)
            if recorded is None:
                deterministic = run_deterministic_checks(rubric, rule_ctx)
                checks = llm_checks(rubric, flags)
                cache_key = (
                    metric_inputs_key(
                        rubric, checks, prompt_version=prompts.version,
                        models=self.ctx.config.models, files=rule_ctx.files, facts=rule_ctx.facts,
                    )
                    if self.options.metric_cache and checks
                    else None
                )  # fmt: skip
                cached = await self._cached_metric(cache_key)
                if cached is not None:
                    logger.info("%s: inputs unchanged, reusing its LLM results", rubric.metric)
                    self.reused.append(rubric.metric)
                    self.ledger.update(cached.ledger)
                    judged = cached.results
                else:
                    span = f"metric {rubric.metric}"
                    with telemetry.tracer().start_as_current_span(
                        span, attributes={"archlens.metric": rubric.metric}
                    ):
                        judged = await evaluate_llm_checks(evaluator, rubric, checks)
                recorded = MetricCheckpoint(
                    results=[*deterministic, *judged],
                    ledger=self.ledger.subset(f"{rubric.metric}:"),
                )
                if cache_key is not None and cached is None:
                    payload = CachedMetric(results=judged, ledger=recorded.ledger)
                    await self.ctx.storage.cache.put(
                        NAMESPACE, cache_key, payload.model_dump_json().encode()
                    )
                await self.cp.save(key, recorded)
                await self._save_records()
            else:
                self.ledger.update(recorded.ledger)
            by_metric[rubric.metric] = recorded.results
            state = self._state()
            if rubric.metric not in state.metrics_done:
                state.metrics_done = sorted([*state.metrics_done, rubric.metric])
                await self._save_state()

        try:
            async with asyncio.TaskGroup() as group:
                for rubric in metrics:
                    group.create_task(one(rubric))
        except ExceptionGroup as failures:
            raise failures.exceptions[0] from failures
        await self._finish("evaluate", started)
        return [result for r in metrics for result in by_metric[r.metric]]

    async def _cached_metric(self, key: str | None) -> CachedMetric | None:
        """The metric cache entry for `key`; None when off, missing, or unreadable."""
        if key is None:
            return None
        data = await self.ctx.storage.cache.get(NAMESPACE, key)
        if data is None:
            return None
        try:
            return CachedMetric.model_validate_json(data)
        except ValueError:
            logger.warning("ignoring an unreadable metric cache entry %s", key[:12])
            return None

    async def _verify(
        self, tools: RepoTools, snapshot: RepoSnapshot, results: list[CheckResult]
    ) -> FindingsCheckpoint:
        sessions = self.ctx.config.models.sessions
        verifier = Verifier(
            self.ctx.llm,
            tools,
            self.rubrics,
            snapshot.commit_sha,
            entailment_prompts=EntailmentPrompts.load(self.ctx.prompts_dir),
            absence_prompts=EntailmentPrompts.load_absence(self.ctx.prompts_dir),
            skeptic_prompts=SkepticPrompts.load(self.ctx.prompts_dir),
            skeptic_limits=sessions.skeptic,
            options=self.options.verifier,
        )
        return FindingsCheckpoint(findings=await verifier.verify(results))

    async def _score(self, findings: list[Finding], profile: RepoProfile) -> ScoresCheckpoint:
        scores = score_assessment(self.rubrics, findings, profile)
        return ScoresCheckpoint(metric_scores=scores.metric_scores, overall=scores.overall)

    async def _report(
        self,
        snapshot: RepoSnapshot,
        facts: FactSet,
        profile: RepoProfile,
        findings: list[Finding],
        boundary: str,
    ) -> AssessmentReport:
        prompts = load_prompts(self.ctx.prompts_dir)
        config = config_fingerprint(
            rubrics=self.rubrics,
            prompt_versions={p.id: p.prompt_version for p in prompts.values()},
            models=self.ctx.config.models,
            tool_runs=facts.tool_runs,
        )
        report = build_report(
            run_id=self.ctx.run_id,
            repo_url=snapshot.repo_url,
            commit_sha=snapshot.commit_sha,
            created_at=self.ctx.clock(),
            config=config,
            profile=profile,
            rubrics=self.rubrics,
            findings=findings,
            tool_runs=facts.tool_runs,
            llm_records=self._records(),
            timings_ms=self.timings,
        )
        synth = await synthesize(
            self.ctx.llm,
            SynthPrompts.load(self.ctx.prompts_dir),
            report,
            boundary=boundary,
            rubrics=self.rubrics,
        )
        if synth.narrative is None:
            logger.warning("report has no narrative: %s", "; ".join(synth.notes) or "no answer")
        for note in synth.notes:
            logger.info("narrative: %s", note)
        return with_narrative(report, synth.narrative, self._records())

    async def _artifacts(self, report: AssessmentReport, snapshot: RepoSnapshot) -> None:
        store, run_id = self.ctx.storage.artifacts, self.ctx.run_id
        await store.put(run_id, "assessment.json", report.model_dump_json(indent=2).encode())
        await store.put(run_id, "report.md", render_markdown(report, self.rubrics).encode())
        await store.put(run_id, "report.html", render_html(report, self.rubrics).encode())
        await store.put(run_id, "findings.sarif", render_sarif(report, self.rubrics).encode())
        if self.options.base is not None:
            changed = await self._changed_since(self.options.base.commit_sha, snapshot)
            comment = render_pr_comment(
                self.options.base, report, self.rubrics, self.reused, changed_paths=changed
            )
            await store.put(run_id, "pr_comment.md", comment.encode())

    async def _changed_since(self, commit_sha: str, snapshot: RepoSnapshot) -> set[str] | None:
        """Paths that differ between the commit `commit_sha` (as recorded by an earlier PR-mode
        run in the cache) and `snapshot`; None when that commit was never recorded."""
        data = await self.ctx.storage.cache.get(FILES_NAMESPACE, commit_sha)
        if data is None:
            return None
        try:
            base = FileHashes.model_validate_json(data)
        except ValueError:
            return None
        return base.changed(FileHashes.of(snapshot.files))

    # --- checkpoints and run state ---

    async def _cached[M: BaseModel](
        self, name: str, model: type[M], compute: Callable[[], Awaitable[M]]
    ) -> M:
        recorded = await self.cp.load(name, model)
        if recorded is not None:
            await self._finish(name, None)
            return recorded
        started = await self._begin(name)
        value = await compute()
        await self.cp.save(name, value)
        await self._finish(name, started)
        return value

    async def _start(self, resume: bool) -> None:
        existing = await self.ctx.storage.run_state.load(self.ctx.run_id)
        if resume and existing is None:
            raise ArchLensError(f"no run {self.ctx.run_id} to resume")
        queued = existing is not None and existing.status == "queued"  # created by the API
        if not resume and existing is not None and not queued:
            raise ArchLensError(f"run {self.ctx.run_id} already exists; resume it instead")
        target = self.options.target
        self.state = existing or queued_state(
            self.ctx.run_id, target if is_remote(target) else None, self.options.ref
        )
        self.state.status = "running"
        self.previous_records = await self.cp.load_records()
        self.timings = await self.cp.load_timings()
        await self._save_state()

    async def _begin(self, name: str) -> float:
        stage = self._stage(name)
        stage.status, stage.started_at, stage.finished_at, stage.error = (
            "running", self.ctx.clock(), None, None
        )  # fmt: skip
        await self._save_state()
        logger.info("stage %s: running", name)
        span = telemetry.tracer().start_span(f"stage {name}", attributes={"archlens.stage": name})
        token = otel_context.attach(trace.set_span_in_context(span))  # LLM spans nest under it
        self._spans[name] = (span, token)
        return time.monotonic()

    async def _finish(self, name: str, started: float | None) -> None:
        stage = self._stage(name)
        if started is not None:
            self.timings[name] = int((time.monotonic() - started) * 1000)
            stage.finished_at = self.ctx.clock()
            await self.cp.save_timings(self.timings)
        stage.status = "done"
        self._end_span(name)
        await self._save_records()
        await self._save_state()

    def _end_span(self, name: str, exc: BaseException | None = None) -> None:
        opened = self._spans.pop(name, None)
        if opened is None:
            return
        span, token = opened
        if exc is not None:
            telemetry.fail(span, exc)
        otel_context.detach(token)
        span.end()

    async def _fail(self, exc: BaseException) -> None:
        state = self._state()
        for stage in state.stages:
            if stage.status == "running":
                stage.status = "failed"
                stage.error = redact(f"{type(exc).__name__}: {exc}")[:500]
                stage.finished_at = self.ctx.clock()
        for name in reversed(list(self._spans)):
            self._end_span(name, exc)
        state.status = "failed"
        await self._save_records()
        await self._save_state()

    def _records(self) -> list[LLMCallRecord]:
        seen = {r.id for r in self.previous_records}
        return [*self.previous_records, *(r for r in self.ctx.llm.records if r.id not in seen)]

    async def _save_records(self) -> None:
        records = self._records()
        await self.cp.save_records(records)
        self._state().cost_usd = round(sum(r.cost_usd for r in records), 6)

    async def _save_state(self) -> None:
        await self.ctx.storage.run_state.save(self._state())

    def _state(self) -> RunState:
        if self.state is None:
            raise ArchLensError("run state used before the run started")
        return self.state

    def _stage(self, name: str) -> StageState:
        return next(s for s in self._state().stages if s.stage == name)


def _select(rubrics: dict[str, Rubric], metrics: tuple[str, ...] | None) -> dict[str, Rubric]:
    if metrics is None:
        return dict(sorted(rubrics.items()))
    unknown = sorted(set(metrics) - set(rubrics))
    if unknown:
        raise ArchLensError(f"unknown metrics {unknown}; available: {sorted(rubrics)}")
    return {name: rubrics[name] for name in sorted(set(metrics))}


async def run_assessment(
    ctx: RunContext, options: RunOptions, *, resume: bool = False
) -> AssessmentReport:
    """Run or resume one assessment. Raises ArchLensError subclasses for stage failures."""
    return await Pipeline(ctx, options).run(resume=resume)
