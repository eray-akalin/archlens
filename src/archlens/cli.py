"""ArchLens command-line interface.

The only module that writes to stdout directly. Commands that are not implemented yet exit with
code 1 and name the milestone (docs/MILESTONES.md) that delivers them.
"""

import asyncio
import json
import logging
import tempfile
from collections import Counter
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from ulid import ULID

from archlens import __version__
from archlens.config import load_config, load_settings
from archlens.errors import ArchLensError, ConfigError
from archlens.eval.config import load_eval_config
from archlens.eval.labels import (
    LABELED_REPO,
    LabelError,
    audit_paths,
    audit_sample,
    audit_sheet,
    dump_audit,
    dump_labels,
    label_sheet,
    label_sheet_path,
    labels_path,
    load_audits,
    load_labels,
    parse_audit_sheet,
    parse_label_sheet,
    read_report,
)
from archlens.eval.metrics import render_table, summarize
from archlens.eval.mutations import registry as mutation_registry
from archlens.eval.repos import DEFAULT_REPOS_FILE, fetch, load_repos
from archlens.eval.runner import EvalOutcome, EvalRunner, build_plan, load_results, project
from archlens.eval.variants import load_variants
from archlens.evidence import SnippetReader
from archlens.facts.runner import collect_facts
from archlens.index.embed import CachedEmbedder
from archlens.ingest import ingest
from archlens.llm.client import LLMClient
from archlens.llm.embedder import ClientEmbedder
from archlens.llm.prompts import DEFAULT_PROMPTS_DIR, update_lock
from archlens.llm.untrusted import new_boundary
from archlens.models import AssessmentReport
from archlens.models.schemas import export_schemas
from archlens.orchestrator.context import RunContext, RunOptions
from archlens.orchestrator.pipeline import ARTIFACTS, run_assessment
from archlens.orchestrator.projection import project_cost
from archlens.profile import build_profile
from archlens.rubric import load_rubrics
from archlens.storage import open_storage

app = typer.Typer(
    name="archlens",
    help="Evidence-backed architecture assessment for Git repositories.",
    no_args_is_help=True,
    add_completion=False,
)
schema_app = typer.Typer(
    help="JSON Schemas generated from the Pydantic contracts.", no_args_is_help=True
)
prompts_app = typer.Typer(help="Prompt template versioning.", no_args_is_help=True)
eval_app = typer.Typer(help="Evaluation harness (docs/EVALUATION.md).")
app.add_typer(schema_app, name="schema")
app.add_typer(prompts_app, name="prompts")
app.add_typer(eval_app, name="eval")


def _not_implemented(milestone: str) -> NoReturn:
    typer.echo(f"not implemented yet (milestone {milestone})", err=True)
    raise typer.Exit(code=1)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version_callback, is_eager=True, help="Show version and exit."
        ),
    ] = False,
) -> None:
    """Evidence-backed architecture assessment for Git repositories."""


@app.command()
def assess(
    target: Annotated[str, typer.Argument(help="Local path or https Git URL to assess.")],
    out: Annotated[Path, typer.Option(help="Directory for run outputs.")] = Path("runs"),
    resume: Annotated[str | None, typer.Option(help="Resume a previous run by run_id.")] = None,
    no_cache: Annotated[
        bool, typer.Option("--no-cache", help="Bypass the LLM exact cache.")
    ] = False,
    metrics: Annotated[
        str | None, typer.Option(help="Comma-separated metrics (default: every rubric).")
    ] = None,
    no_scanners: Annotated[
        bool, typer.Option("--no-scanners", help="Extractors only (no external tools).")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the projected LLM cost and exit.")
    ] = False,
    cassettes: Annotated[
        Path | None,
        typer.Option(hidden=True, help="Cassette dir for ARCHLENS_LLM_RECORD_MODE=record/replay."),
    ] = None,
) -> None:
    """Run the full pipeline: ingest, facts, profile, index, evaluate, verify, score, report."""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    try:
        settings = load_settings()
        config = load_config(settings)
        rubrics = load_rubrics()
        selected = tuple(m.strip() for m in metrics.split(",") if m.strip()) if metrics else None
        names = sorted(selected or rubrics)
        unknown = sorted(set(names) - set(rubrics))
        if unknown:
            raise ConfigError("--metrics", ",".join(unknown), f"available: {sorted(rubrics)}")
    except ArchLensError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if dry_run:
        projection = project_cost(config, [rubrics[name] for name in names])
        for line in projection.lines:
            typer.echo(f"  {line.item:40} ${line.usd:.4f}")
        budget = settings.run_budget_usd
        typer.echo(f"projected (upper bound): ${projection.total:.4f}; run budget ${budget:.2f}")
        return
    run_id = resume or str(ULID())
    storage = open_storage(settings)
    boundary = new_boundary()
    cache = None if no_cache else storage.cache
    llm = LLMClient.from_config(
        config, cache_store=cache, boundary=boundary, cassette_dir=cassettes
    )
    embedder = CachedEmbedder(
        ClientEmbedder(llm, config.models.roles.embed.deployment), storage.cache
    )
    ctx = RunContext(
        run_id=run_id,
        config=config,
        storage=storage,
        llm=llm,
        embedder=embedder,
        boundary=boundary,
        work_dir=settings.data_dir / "runs" / run_id,
        tools_dir=settings.tools_dir,
        rubrics=rubrics,
    )
    options = RunOptions(target=target, metrics=selected, scanners=not no_scanners)

    async def run() -> AssessmentReport:
        try:
            return await run_assessment(ctx, options, resume=resume is not None)
        finally:
            await llm.aclose()

    typer.echo(f"run {run_id}")
    try:
        report = asyncio.run(run())
    except ArchLensError as exc:
        typer.echo(f"run {run_id} failed: {exc}", err=True)
        typer.echo(f"resume with: archlens assess {target} --resume {run_id}", err=True)
        raise typer.Exit(code=1) from exc
    run_out = out / run_id
    run_out.mkdir(parents=True, exist_ok=True)

    async def copy() -> None:
        for name in ARTIFACTS:
            data = await storage.artifacts.get(run_id, name)
            if data is not None:
                (run_out / name).write_bytes(data)

    asyncio.run(copy())
    for metric in report.metric_scores:
        score = f"{metric.score:.1f}" if metric.score is not None else "-"
        typer.echo(f"  {metric.metric:14} {score:>5}  {metric.status}")
    overall = f"{report.overall_score:.1f}" if report.overall_score is not None else "not computed"
    typer.echo(f"overall: {overall}; cost ${report.cost.usd:.4f}; report: {run_out}/report.md")


@app.command()
def facts(
    target: Annotated[str, typer.Argument(help="Local path or https Git URL.")],
    out: Annotated[Path, typer.Option(help="Output directory.")] = Path("runs"),
    no_scanners: Annotated[
        bool, typer.Option("--no-scanners", help="Extractors only (no external tools).")
    ] = False,
) -> None:
    """Extract deterministic facts only (no LLM calls) → <out>/<commit>/facts.jsonl."""
    settings = load_settings()
    config = load_config(settings)
    with tempfile.TemporaryDirectory(prefix="archlens-") as tmp:
        work = Path(tmp)
        try:
            ingested = ingest(target, workdir=work / "clone")
        except ArchLensError as exc:
            typer.echo(f"ingest failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        result = collect_facts(
            ingested.root,
            ingested.snapshot,
            workdir=work / "tools",
            tools=config.tools,
            tools_dir=settings.tools_dir,
            scanners=not no_scanners,
        )
        # The clone is deleted with `tmp`: anything that reads files must run inside this block.
        reader = SnippetReader(ingested.root, result.redactor)
        profile = build_profile(ingested.snapshot, result.facts, reader)
    run_dir = out / ingested.snapshot.commit_sha[:12]
    run_dir.mkdir(parents=True, exist_ok=True)
    fact_set = result.facts
    with (run_dir / "facts.jsonl").open("w", encoding="utf-8") as handle:
        for fact in fact_set.facts:
            handle.write(fact.model_dump_json() + "\n")
    (run_dir / "tool_runs.json").write_text(
        json.dumps([r.model_dump() for r in fact_set.tool_runs], indent=2) + "\n"
    )
    (run_dir / "profile.json").write_text(profile.model_dump_json(indent=2) + "\n")
    counts = Counter(f.kind for f in fact_set.facts)
    typer.echo(
        f"{len(ingested.snapshot.files)} files, {len(fact_set.facts)} facts → {run_dir}/facts.jsonl"
    )
    for run in fact_set.tool_runs:
        detail = f" ({run.error})" if run.error else ""
        typer.echo(f"  {run.status:8} {run.tool:14} {run.fact_count:5} facts{detail}")
    typer.echo("  by kind: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    typer.echo("  flags: " + ", ".join(flag for flag, on in profile.flags.items() if on))


@eval_app.callback(invoke_without_command=True)
def eval_(
    ctx: typer.Context,
    config: Annotated[
        Path | None, typer.Option(help="Eval config YAML, e.g. eval/configs/full.yaml.")
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print projected cost only.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the spending confirmation.")] = False,
) -> None:
    """Run the evaluation harness (EVALUATION.md §7); subcommands: fetch, report, label-sheet,
    audit."""
    if ctx.invoked_subcommand is not None:
        return
    if config is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(code=1)
    try:
        settings = load_settings()
        app_config = load_config(settings)
        rubrics = load_rubrics()
        eval_config = load_eval_config(config)
        variants_file = eval_config.variants_file
        variants = load_variants(variants_file) if variants_file.exists() else []
        mutations = mutation_registry()
        plan = build_plan(
            eval_config,
            repos=load_repos(eval_config.repos_file),
            variants=variants,
            mutations=mutations,
            rubrics=rubrics,
        )
    except ArchLensError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    for problem in plan.problems:
        typer.echo(f"  problem: {problem}", err=True)
    projection = project(plan, app_config, rubrics)
    generic = sorted(m.id for m in mutations.values() if m.generic and not m.injection)
    for run in plan.runs:
        cache = "cache on" if run.use_cache else "cache off"
        spec = plan.variants.get(run.variant)
        if spec is None:
            what = "clean repo"
        elif spec.all_generic:
            what = "generic, if their precondition holds: " + ", ".join(generic)
        else:
            what = ", ".join(spec.mutations)
        typer.echo(f"  {run.name:16} {cache:9} ~${projection.per_run:.4f}  {what}")
    typer.echo(
        f"{len(plan.runs)} runs, projected ${projection.total:.2f} (upper bound); "
        f"budget ${projection.budget:.2f}"
    )
    if plan.problems:
        raise typer.Exit(code=1)
    if dry_run:
        return
    if not yes and not typer.confirm("Spend real money on these runs?", default=False):
        raise typer.Exit(code=1)
    try:
        labels = load_labels(labels_path(LABELED_REPO))
    except ArchLensError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    storage = open_storage(settings)
    clients: list[LLMClient] = []

    def context_for(run_id: str, use_cache: bool) -> RunContext:
        boundary = new_boundary()
        cache = storage.cache if use_cache else None
        llm = LLMClient.from_config(app_config, cache_store=cache, boundary=boundary)
        clients.append(llm)
        embed = app_config.models.roles.embed.deployment
        return RunContext(
            run_id=run_id,
            config=app_config,
            storage=storage,
            llm=llm,
            embedder=CachedEmbedder(ClientEmbedder(llm, embed), storage.cache),
            boundary=boundary,
            work_dir=settings.data_dir / "runs" / run_id,
            tools_dir=settings.tools_dir,
            rubrics=rubrics,
        )

    runner = EvalRunner(
        plan=plan,
        mutations=mutations,
        cache_dir=settings.data_dir / "eval" / "repos",
        results_dir=Path("eval/results"),
        context_factory=context_for,
        eval_run_id=f"{eval_config.name}-{ULID()}",
        app_config=app_config,
        tools_dir=settings.tools_dir,
        labels=labels,
    )

    async def go() -> EvalOutcome:
        try:
            return await runner.run(projection.per_run)
        finally:
            for client in clients:
                await client.aclose()

    try:
        outcome = asyncio.run(go())
    except ArchLensError as exc:
        typer.echo(f"eval failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"results: {outcome.directory} ({len(outcome.records)} runs)")
    if outcome.skipped:
        typer.echo(f"skipped for budget: {', '.join(outcome.skipped)}")


@eval_app.command("report")
def eval_report(
    results: Annotated[Path, typer.Argument(help="eval/results/<run_id> directory.")],
) -> None:
    """Recompute summary metrics (with labels and audit answers, if any) and rewrite table.md."""
    try:
        records = load_results(results)
        config = load_eval_config(results / "config.yaml")
        labels = load_labels(labels_path(LABELED_REPO))
        audits = load_audits(audit_paths(results.name)[1])
    except ArchLensError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    table = render_table(summarize(records, labels=labels, audits=audits), config.name)
    (results / "table.md").write_text(table)
    typer.echo(table)


@eval_app.command("label-sheet")
def eval_label_sheet(
    repo_id: Annotated[str, typer.Argument(help="Eval repo id, e.g. primary.")],
    report: Annotated[
        Path | None,
        typer.Option(help="Assessment JSON (or eval run record) whose findings the sheet shows."),
    ] = None,
    read: Annotated[
        bool, typer.Option("--read", help="Read the filled sheet into eval/labels/<repo>.yaml.")
    ] = False,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing sheet.")] = False,
    repos: Annotated[Path, typer.Option(help="Eval set file.")] = DEFAULT_REPOS_FILE,
) -> None:
    """Write the manual-label sheet for an eval repo, or read a filled one back (EVALUATION §3)."""
    try:
        repo = next((r for r in load_repos(repos) if r.id == repo_id), None)
        if repo is None:
            raise LabelError(f"unknown eval repo {repo_id!r}")
        rubrics = load_rubrics()
        sheet = label_sheet_path(repo_id)
        if read:
            metric_of = {c.id: r.metric for r in rubrics.values() for c in r.checks}
            labels = parse_label_sheet(sheet.read_text(encoding="utf-8"), metric_of)
            target = labels_path(repo_id)
            target.write_text(dump_labels(repo_id, repo.commit, labels))
            metrics = {metric_of[c] for c in labels}
            typer.echo(f"{len(labels)} labels over {len(metrics)} metrics → {target}")
            if len(metrics) < 6:
                typer.echo("  note: EVALUATION.md §3 asks for labels in at least 6 metrics")
            return
        if sheet.exists() and not force:
            raise LabelError(f"{sheet} exists and may hold your labels; --force overwrites it")
        current = read_report(report) if report else None
        sheet.parent.mkdir(parents=True, exist_ok=True)
        sheet.write_text(label_sheet(repo_id, repo.commit, rubrics, current))
    except (ArchLensError, OSError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"wrote {sheet}")


@eval_app.command("audit")
def eval_audit(
    results: Annotated[Path, typer.Argument(help="eval/results/<run_id> directory.")],
    sample: Annotated[int, typer.Option(help="Number of verified findings to audit.")] = 30,
    seed: Annotated[int, typer.Option(help="Sampling seed.")] = 0,
    read: Annotated[
        bool, typer.Option("--read", help="Read the filled sheet into its audit YAML.")
    ] = False,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing sheet.")] = False,
) -> None:
    """Write the verifier-audit sheet for an eval run, or read a filled one back (EVALUATION §4)."""
    sheet, parsed = audit_paths(results.name)
    try:
        if read:
            answers = parse_audit_sheet(sheet.read_text(encoding="utf-8"))
            parsed.write_text(dump_audit(results.name, answers))
            agreed = sum(answers.values())
            typer.echo(f"{len(answers)} answers, {agreed} agree → {parsed}")
            return
        if sheet.exists() and not force:
            raise LabelError(f"{sheet} exists and may hold your answers; --force overwrites it")
        chosen = audit_sample(load_results(results), sample, seed)
        if not chosen:
            raise LabelError(f"{results}: no verified LLM findings to audit")
        sheet.parent.mkdir(parents=True, exist_ok=True)
        sheet.write_text(audit_sheet(results.name, chosen))
    except (ArchLensError, OSError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"wrote {sheet} ({len(chosen)} findings)")


@eval_app.command("fetch")
def eval_fetch(
    repos: Annotated[Path, typer.Option(help="Eval set file.")] = DEFAULT_REPOS_FILE,
) -> None:
    """Clone every eval repository at its pinned commit into the data dir's eval cache."""
    settings = load_settings()
    cache = settings.data_dir / "eval" / "repos"
    try:
        for repo in load_repos(repos):
            fetched = fetch(repo, cache)
            state = "cached" if fetched.cached else "cloned"
            typer.echo(f"  {state:7} {repo.id:8} {repo.commit[:12]}  {fetched.path}")
    except ArchLensError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@schema_app.command("export")
def schema_export(
    out: Annotated[Path, typer.Option(help="Output directory.")] = Path("schemas"),
) -> None:
    """Write JSON Schemas for all top-level contracts."""
    written = export_schemas(out)
    typer.echo(f"wrote {len(written)} schemas to {out}")


@prompts_app.command("lock")
def prompts_lock(
    directory: Annotated[Path, typer.Option(help="Prompt directory.")] = DEFAULT_PROMPTS_DIR,
) -> None:
    """Update prompts/prompts.lock after a prompt version bump."""
    try:
        lock = update_lock(directory)
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    for prompt_id, entry in lock.items():
        typer.echo(f"{prompt_id}@{entry['version']}+{entry['sha256'][:8]}")


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port.")] = 8000,
) -> None:
    """Start the HTTP API (only `GET /healthz` until M4.3)."""
    import uvicorn

    from archlens.api import create_app

    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


@app.command()
def worker(
    once: Annotated[bool, typer.Option("--once", help="Process one queued job and exit.")] = False,
) -> None:
    """Consume assessment jobs from the queue."""
    _not_implemented("M4.3")
