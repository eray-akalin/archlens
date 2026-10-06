"""Hand-built facts and contexts for rule tests."""

from pathlib import Path

from pydantic import JsonValue

from archlens.config import ToolsConfig
from archlens.evidence import SnippetReader
from archlens.facts.base import make_fact
from archlens.facts.runner import collect_facts
from archlens.ingest.snapshot import build_snapshot
from archlens.models import (
    CheckResult,
    Evidence,
    Fact,
    FactSet,
    IngestLimits,
    RepoProfile,
    RuleOutcome,
    ScanEvidence,
    Severity,
    ToolRunRecord,
    ToolStatus,
)
from archlens.profile import build_profile
from archlens.rubric import RuleContext, get_rule, load_rubrics, run_rule
from archlens.security.redact import Redactor

# Every scanner/extractor a rule in M2.2 depends on.
TOOLS = ("gitleaks", "osv-scanner", "semgrep", "actionlint", "ast:ci", "ast:tests", "ast:metrics")


def tool_run(tool: str, status: ToolStatus = "ok", error: str | None = None) -> ToolRunRecord:
    return ToolRunRecord(
        tool=tool,
        version="9.9.9",
        command=[tool],
        status=status,
        duration_ms=1,
        fact_count=0,
        error=error or (f"{tool} broke" if status in ("error", "timeout") else None),
    )


def fact(
    kind: str,
    /,
    severity: Severity | None = None,
    evidence: list[Evidence] | None = None,
    **attributes: JsonValue,
) -> Fact:
    where = str(attributes.get("path", kind))
    default: list[Evidence] = [
        ScanEvidence(tool="test", tool_version="1", query=where, result_count=1)
    ]
    return make_fact(kind, "test", attributes, default if evidence is None else evidence, severity)


def profile(**flags: bool) -> RepoProfile:
    return RepoProfile(
        languages={},
        frameworks=[],
        package_managers=[],
        ci_systems=[],
        test_frameworks=[],
        flags=flags,
        entrypoints=[],
    )


def make_ctx(
    root: Path,
    facts: list[Fact] | None = None,
    *,
    files: dict[str, str] | None = None,
    status: dict[str, ToolStatus | None] | None = None,
    redactor: Redactor | None = None,
) -> RuleContext:
    """Context over `root` (created, with `files` written). Every tool in TOOLS ran `ok` unless
    `status` overrides it; `None` there means the tool never ran."""
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in (files or {}).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(content)
    overrides = status or {}
    runs = [tool_run(tool, s) for tool in TOOLS if (s := overrides.get(tool, "ok")) is not None]
    snapshot = build_snapshot(root, limits=IngestLimits())
    fact_set = FactSet(commit_sha="0" * 40, facts=facts or [], tool_runs=runs)
    return RuleContext.create(root, fact_set, profile(), snapshot.files, redactor)


def run(name: str, ctx: RuleContext, **params: JsonValue) -> RuleOutcome:
    registered = get_rule(name)
    assert registered is not None, name
    return registered.func(ctx, registered.parse_params(params))


def ci_workflow(path: str = ".github/workflows/ci.yml", **attributes: JsonValue) -> Fact:
    base: dict[str, JsonValue] = {
        "path": path,
        "system": "github_actions",
        "name": "ci",
        "triggers": ["push"],
        "permissions": None,
        "jobs": ["build"],
        "job_permissions": {"build": None},
        "environments": {"build": None},
    }
    evidence: list[Evidence] = [
        ScanEvidence(tool="test", tool_version="1", query=path, result_count=1)
    ]
    return make_fact("ci_workflow", "test", base | attributes, evidence)


def ci_step(
    run: str | None = None,
    uses: str | None = None,
    run_kind: str = "other",
    *,
    path: str = ".github/workflows/ci.yml",
    system: str = "github_actions",
    pinned: bool | None = None,
) -> Fact:
    return fact(
        "ci_step",
        path=path,
        system=system,
        job="build",
        uses=uses,
        run=run,
        run_kind=run_kind,
        pinned=pinned,
        with_keys=[],
    )


RUBRICS_DIR = Path(__file__).parents[3] / "rubrics"
TINY_SERVICE_METRICS = ("security", "testing", "cicd")


def deterministic_results(
    root: Path,
    tmp_path: Path,
    *,
    scanners: bool = False,
    tools: ToolsConfig | None = None,
    tools_dir: Path | None = None,
) -> dict[str, CheckResult]:
    snapshot = build_snapshot(root, limits=IngestLimits())
    found = collect_facts(
        root,
        snapshot,
        workdir=tmp_path / "work",
        tools=tools or ToolsConfig(tools={}),
        tools_dir=tools_dir or tmp_path / "tools",
        scanners=scanners,
    )
    profile = build_profile(snapshot, found.facts, SnippetReader(root))
    ctx = RuleContext.create(root, found.facts, profile, snapshot.files, found.redactor)
    rubrics = load_rubrics(RUBRICS_DIR)
    return {
        check.id: run_rule(check, rubrics[metric], ctx)
        for metric in TINY_SERVICE_METRICS
        for check in rubrics[metric].checks
        if check.type == "deterministic"
        and not check.retired
        and check.applies_when.holds(profile.flags)
    }


def first_scan(outcome: RuleOutcome) -> ScanEvidence:
    """The outcome's leading ScanEvidence (rules put the query record first)."""
    first = outcome.evidence[0]
    assert isinstance(first, ScanEvidence), first
    return first
