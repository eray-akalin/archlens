"""Eval runner (EVALUATION.md §7).

`build_plan` validates a config against the eval set, the variants file and the mutation registry
(EVALUATION.md §2.1 static rules) and expands the suite into runs. `project` prices the plan with
the `--dry-run` heuristics. `EvalRunner.run` executes it: each variant is a fresh copy of the
fetched base checkout with its mutations applied (nothing is ever fixed back), assessed as a local
path; base runs have the exact LLM cache off (stability), the others on (a crashed eval doesn't pay
twice); a run starts only while spent + its projected cost stays within `budget_usd`. Results go
to `eval/results/<run_id>/{config.yaml, variants/<name>.json, summary.json, table.md}` and are
append-only.
"""

import json
import shutil
import tempfile
import time
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import yaml

from archlens.config import AppConfig
from archlens.errors import ArchLensError, ConfigError
from archlens.eval.config import BASE, AppliedRecord, ChangedRange, EvalConfig, VariantRun
from archlens.eval.metrics import Audit, Label, Summary, render_table, summarize
from archlens.eval.mutation import Mutation
from archlens.eval.repos import EvalRepo, checkout_path, marker_path
from archlens.eval.variants import VariantSpec, apply_variant, static_problems
from archlens.evidence import SnippetReader
from archlens.facts.runner import collect_facts
from archlens.ingest.snapshot import build_snapshot
from archlens.models import FactSet, IngestLimits, RepoProfile, Rubric
from archlens.orchestrator.context import RunContext, RunOptions
from archlens.orchestrator.pipeline import run_assessment
from archlens.orchestrator.projection import project_cost
from archlens.profile import build_profile
from archlens.verify.pipeline import VerifierOptions

ContextFactory = Callable[[str, bool], RunContext]  # (assessment run id, use exact cache)


@dataclass(frozen=True)
class PlannedRun:
    repo: str
    variant: str  # "base" or a variant id
    index: int
    use_cache: bool

    @property
    def name(self) -> str:
        return f"{self.repo}.{self.variant}.{self.index}"


@dataclass(frozen=True)
class EvalPlan:
    config: EvalConfig
    repos: dict[str, EvalRepo]
    variants: dict[str, VariantSpec]
    runs: list[PlannedRun]
    problems: list[str]


def build_plan(
    config: EvalConfig,
    *,
    repos: list[EvalRepo],
    variants: list[VariantSpec],
    mutations: Mapping[str, Mutation],
    rubrics: Mapping[str, Rubric],
) -> EvalPlan:
    """Validate and expand the suite; `problems` lists everything that blocks a run."""
    by_repo = {r.id: r for r in repos}
    by_variant = {v.id: v for v in variants}
    check_metric = {c.id: r.metric for r in rubrics.values() for c in r.checks}
    problems = [f"feature not supported yet (M5.1): {f}" for f in config.features.unsupported()]
    runs: list[PlannedRun] = []
    seen: set[tuple[str, str]] = set()
    for entry in config.suite:
        if entry.repo not in by_repo:
            problems.append(f"suite: unknown repo {entry.repo!r}")
            continue
        if (entry.repo, entry.variant) in seen:
            problems.append(f"suite: {entry.repo}/{entry.variant} listed twice; use `runs`")
        seen.add((entry.repo, entry.variant))
        if entry.variant != BASE:
            spec = by_variant.get(entry.variant)
            if spec is None:
                problems.append(f"suite: unknown variant {entry.variant!r}")
                continue
            if spec.repo != entry.repo:
                problems.append(f"suite: variant {spec.id} is for {spec.repo}, not {entry.repo}")
            problems += static_problems(spec, mutations, check_metric)
        runs += [
            PlannedRun(entry.repo, entry.variant, i, use_cache=entry.variant != BASE)
            for i in range(entry.runs)
        ]
    return EvalPlan(config, by_repo, by_variant, runs, problems)


@dataclass(frozen=True)
class Projection:
    per_run: Decimal
    runs: int
    budget: Decimal

    @property
    def total(self) -> Decimal:
        return self.per_run * self.runs


def project(plan: EvalPlan, app_config: AppConfig, rubrics: Mapping[str, Rubric]) -> Projection:
    per_run = project_cost(app_config, list(rubrics.values())).total
    return Projection(per_run, len(plan.runs), Decimal(str(plan.config.budget_usd)))


@dataclass(frozen=True)
class EvalOutcome:
    directory: Path
    records: list[VariantRun]
    skipped: list[str]  # runs not started because of the budget
    summary: Summary


@dataclass
class EvalRunner:
    plan: EvalPlan
    mutations: Mapping[str, Mutation]
    cache_dir: Path  # fetched checkouts (`archlens eval fetch`)
    results_dir: Path  # eval/results
    context_factory: ContextFactory
    eval_run_id: str
    app_config: AppConfig
    tools_dir: Path
    scanners: bool = True  # scanners for assessments and mutation preconditions (off in tests)
    labels: Mapping[str, Label] = field(default_factory=dict[str, Label])
    audits: list[Audit] = field(default_factory=list[Audit])
    _base_facts: dict[str, tuple[FactSet, RepoProfile]] = field(
        default_factory=dict[str, tuple[FactSet, RepoProfile]]
    )

    async def run(self, per_run_estimate: Decimal) -> EvalOutcome:
        if self.plan.problems:
            raise ConfigError("eval config", "plan", "; ".join(self.plan.problems))
        out = self.results_dir / self.eval_run_id
        if out.exists():
            raise ArchLensError(f"{out} exists; eval results are append-only")
        (out / "variants").mkdir(parents=True)
        (out / "config.yaml").write_text(
            yaml.safe_dump(self.plan.config.model_dump(mode="json"), sort_keys=False)
        )
        budget = Decimal(str(self.plan.config.budget_usd))
        spent = Decimal(0)
        records: list[VariantRun] = []
        skipped: list[str] = []
        for planned in self.plan.runs:
            if spent + per_run_estimate > budget:
                skipped.append(planned.name)
                continue
            record = await self._one(planned)
            (out / "variants" / f"{planned.name}.json").write_text(record.model_dump_json(indent=2))
            records.append(record)
            spent += Decimal(str(record.report.cost.usd))
        summary = summarize(records, labels=self.labels, audits=self.audits)
        (out / "summary.json").write_text(
            json.dumps({"summary": summary.model_dump(mode="json"), "skipped": skipped}, indent=2)
        )
        (out / "table.md").write_text(render_table(summary, self.plan.config.name))
        return EvalOutcome(out, records, skipped, summary)

    async def _one(self, planned: PlannedRun) -> VariantRun:
        repo = self.plan.repos[planned.repo]
        base = checkout_path(repo, self.cache_dir)
        if not marker_path(base).is_file():
            raise ConfigError(str(base), planned.repo, "not fetched; run `archlens eval fetch`")
        with tempfile.TemporaryDirectory(prefix="archlens-eval-") as tmp:
            copy = Path(tmp) / "repo"
            shutil.copytree(base, copy, symlinks=True, ignore=shutil.ignore_patterns(".git"))
            applied: list[AppliedRecord] = []
            skipped: list[str] = []
            if planned.variant != BASE:
                spec, skipped = self._effective(self.plan.variants[planned.variant], repo, base)
                seed = self.plan.config.seed ^ zlib.crc32(planned.name.encode())
                applied = [
                    AppliedRecord(
                        id=a.mutation.id,
                        expected=dict(a.mutation.expected),
                        may_affect=list(a.mutation.may_affect),
                        absence=a.mutation.absence,
                        injection=a.mutation.injection,
                        changed=[
                            ChangedRange(path=c.path, start_line=c.start_line, end_line=c.end_line)
                            for c in a.result.changed
                        ],
                    )
                    for a in apply_variant(spec, copy, self.mutations, seed)
                ]
            features = self.plan.config.features
            options = RunOptions(
                target=str(copy),
                scanners=self.scanners,
                verifier=VerifierOptions(
                    entailment=features.verifier == "full", skeptic=features.skeptic
                ),
            )
            run_id = f"{self.eval_run_id}-{planned.name}".replace(".", "-")
            started = time.monotonic()
            report = await run_assessment(self.context_factory(run_id, planned.use_cache), options)
            seconds = round(time.monotonic() - started, 1)
        return VariantRun(
            name=planned.name,
            repo=planned.repo,
            variant=planned.variant,
            index=planned.index,
            commit=f"{repo.commit}+{planned.variant}",
            mutations=applied,
            skipped=skipped,
            seconds=seconds,
            report=report,
        )

    def _effective(
        self, spec: VariantSpec, repo: EvalRepo, base: Path
    ) -> tuple[VariantSpec, list[str]]:
        """The variant with mutations whose precondition fails on the base repo dropped (a pair
        goes with its defect); `all_generic` expands to every applicable generic mutation."""
        facts, profile = self._facts(repo, base)
        if spec.all_generic:
            entries = [
                m.id for m in sorted(self.mutations.values(), key=lambda m: m.id)
                if m.generic and not m.injection
            ]  # fmt: skip
        else:
            entries = list(spec.mutations)
        kept: list[str] = []
        skipped: list[str] = []
        for entry in entries:
            group = [part.strip() for part in entry.split("+")]
            failing = [m for m in group if not self.mutations[m].precondition(facts, profile)]
            if failing:
                skipped += group
            else:
                kept.append(entry)
        effective = spec.model_copy(update={"mutations": kept, "all_generic": False})
        return effective, skipped

    def _facts(self, repo: EvalRepo, base: Path) -> tuple[FactSet, RepoProfile]:
        if repo.id not in self._base_facts:
            snapshot = build_snapshot(base, limits=IngestLimits())
            with tempfile.TemporaryDirectory(prefix="archlens-eval-facts-") as work:
                found = collect_facts(
                    base,
                    snapshot,
                    workdir=Path(work),
                    tools=self.app_config.tools,
                    tools_dir=self.tools_dir,
                    scanners=self.scanners,
                )
            profile = build_profile(snapshot, found.facts, SnippetReader(base, found.redactor))
            self._base_facts[repo.id] = (found.facts, profile)
        return self._base_facts[repo.id]


def load_results(directory: Path) -> list[VariantRun]:
    """Every recorded run of an eval results directory, in name order."""
    return [
        VariantRun.model_validate_json(p.read_text())
        for p in sorted((directory / "variants").glob("*.json"))
    ]
