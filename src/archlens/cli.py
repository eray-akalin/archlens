"""ArchLens command-line interface.

The only module that writes to stdout directly. Commands that are not implemented yet exit with
code 1 and name the milestone (docs/MILESTONES.md) that delivers them.
"""

import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from archlens import __version__
from archlens.config import load_config, load_settings
from archlens.errors import ArchLensError
from archlens.evidence import SnippetReader
from archlens.facts.runner import collect_facts
from archlens.ingest import ingest
from archlens.models.schemas import export_schemas
from archlens.profile import build_profile

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
app.add_typer(schema_app, name="schema")
app.add_typer(prompts_app, name="prompts")


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
) -> None:
    """Run the full pipeline: ingest, facts, profile, index, evaluate, verify, score, report."""
    _not_implemented("M2.8")


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


@app.command("eval")
def eval_(
    config: Annotated[Path, typer.Option(help="Eval config YAML, e.g. eval/configs/full.yaml.")],
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print projected cost only.")] = False,
) -> None:
    """Run the evaluation harness against the eval set."""
    _not_implemented("M3.3")


@schema_app.command("export")
def schema_export(
    out: Annotated[Path, typer.Option(help="Output directory.")] = Path("schemas"),
) -> None:
    """Write JSON Schemas for all top-level contracts."""
    written = export_schemas(out)
    typer.echo(f"wrote {len(written)} schemas to {out}")


@prompts_app.command("lock")
def prompts_lock() -> None:
    """Update prompts/prompts.lock after a prompt version bump."""
    _not_implemented("M2.4")


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port.")] = 8000,
) -> None:
    """Start the HTTP API."""
    _not_implemented("M4.3")


@app.command()
def worker(
    once: Annotated[bool, typer.Option("--once", help="Process one queued job and exit.")] = False,
) -> None:
    """Consume assessment jobs from the queue."""
    _not_implemented("M4.3")
