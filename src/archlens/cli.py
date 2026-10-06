"""ArchLens command-line interface.

The only module that writes to stdout directly. Commands that are not implemented yet exit with
code 1 and name the milestone (docs/MILESTONES.md) that delivers them.
"""

from pathlib import Path
from typing import Annotated, NoReturn

import typer

from archlens import __version__

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
    path: Annotated[Path, typer.Argument(help="Local repository path.")],
) -> None:
    """Extract deterministic facts only (no LLM calls)."""
    _not_implemented("M1.5")


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
    _not_implemented("M0.2")


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
