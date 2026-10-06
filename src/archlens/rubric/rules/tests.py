"""`tests.*` rules over `test_file`, `file_metrics` and `ci_step` facts (TEST-01, -02, -04)."""

import re

from pydantic import Field

from archlens.globs import glob_match
from archlens.models import Evidence, RuleOutcome
from archlens.rubric.context import RuleContext
from archlens.rubric.registry import RuleParams, rule
from archlens.rubric.rules._common import (
    MAX_EVIDENCE,
    attr_bool,
    attr_int,
    attr_str,
    fact_evidence,
    missing_data,
    plural,
    scan,
)

TESTS_TOOL = "ast:tests"
METRICS_TOOL = "ast:metrics"
CI_TOOL = "ast:ci"

# A file whose presence alone means coverage is configured.
COVERAGE_FILES = (
    "**/.coveragerc",
    "**/.nycrc",
    "**/.nycrc.{json,yml,yaml}",
    "**/{.,}codecov.{yml,yaml}",
    "**/.c8rc{,.json}",
)
# Config files that configure coverage when their (redacted) text matches.
COVERAGE_CONTENT: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("**/pyproject.toml", re.compile(r"(?m)^\[tool\.coverage|--cov\b")),
    ("**/{setup.cfg,tox.ini}", re.compile(r"(?m)^\[coverage:|--cov\b")),
    ("**/pytest.ini", re.compile(r"--cov\b")),
    (
        "**/{jest,vitest,vite,karma}.conf{ig,}.{js,cjs,mjs,ts,cts,mts,json}",
        re.compile(r"coverage", re.I),
    ),
    ("**/package.json", re.compile(r"--coverage\b|collectCoverage|\"(nyc|c8)\"|\b(nyc|c8)\s")),
    ("**/{*.csproj,Directory.Build.props,Directory.Packages.props}", re.compile(r"coverlet", re.I)),
    ("**/{pom.xml,build.gradle,build.gradle.kts}", re.compile(r"jacoco|kover", re.I)),
    ("**/Makefile", re.compile(r"--cov\b|\bcoverage\s+(run|report|xml|html)\b|-coverprofile")),
)
# Coverage flags or upload actions in CI steps.
COVERAGE_STEP = re.compile(
    r"--cov\b|\bcoverage\s+(run|report|xml|html|lcov)\b|--coverage\b|\b(nyc|c8)\s"
    r"|XPlat Code Coverage|CollectCoverage|-coverprofile|-cover\b"
    r"|\bjacoco|\bkover|codecov|coveralls",
    re.I,
)
MAX_CONFIG_FILES = 200  # bound on config files read per run


class PresentParams(RuleParams):
    min_test_files: int = Field(default=1, ge=1)


@rule("tests.present")
def present(ctx: RuleContext, params: PresentParams) -> RuleOutcome:
    """pass if at least `min_test_files` test files were detected, else fail."""
    if (missing := missing_data(ctx, TESTS_TOOL)) is not None:
        return missing
    files = ctx.facts.by_kind("test_file")
    tests = sum(attr_int(f, "test_count") for f in files)
    frameworks = sorted({attr_str(f, "framework") or "?" for f in files})
    evidence: list[Evidence] = [scan(ctx, TESTS_TOOL, "test files", len(files))]
    evidence += fact_evidence(files, MAX_EVIDENCE - 1)
    if len(files) >= params.min_test_files:
        return RuleOutcome(
            verdict="pass",
            claim=f"{plural(len(files), 'test file')} with {plural(tests, 'test')} "
            f"({', '.join(frameworks)}).",
            evidence=evidence,
        )
    return RuleOutcome(
        verdict="fail",
        claim=f"{plural(len(files), 'test file')} found; "
        f"at least {params.min_test_files} expected.",
        evidence=evidence,
    )


class RatioParams(RuleParams):
    pass_at: float = 0.3
    partial_at: float = 0.1


@rule("tests.ratio")
def ratio(ctx: RuleContext, params: RatioParams) -> RuleOutcome:
    """Test LOC / non-test source LOC over non-generated `file_metrics`; NA without source."""
    if (missing := missing_data(ctx, METRICS_TOOL)) is not None:
        return missing
    metrics = [f for f in ctx.facts.by_kind("file_metrics") if not attr_bool(f, "is_generated")]
    tests = [f for f in metrics if attr_bool(f, "is_test")]
    test_loc = sum(attr_int(f, "loc") for f in tests)
    source_loc = sum(attr_int(f, "loc") for f in metrics if not attr_bool(f, "is_test"))
    query = f"test LOC {test_loc} / source LOC {source_loc}"
    evidence: list[Evidence] = [scan(ctx, METRICS_TOOL, query, len(tests))]
    if source_loc == 0:
        return RuleOutcome(
            verdict="not_applicable",
            claim="No non-test source code to compare tests against.",
            evidence=evidence,
            reason="no_source_code",
        )
    value = test_loc / source_loc
    verdict = (
        "pass" if value >= params.pass_at else "partial" if value >= params.partial_at else "fail"
    )
    return RuleOutcome(
        verdict=verdict,
        claim=f"Test-to-source LOC ratio is {value:.2f} ({test_loc} test / {source_loc} source "
        f"LOC); pass at {params.pass_at}, partial at {params.partial_at}.",
        evidence=evidence,
    )


class CoverageParams(RuleParams):
    pass


@rule("tests.coverage_configured")
def coverage_configured(ctx: RuleContext, params: CoverageParams) -> RuleOutcome:
    """pass on a coverage config file, coverage settings in a known config file, or coverage
    flags/upload actions in a CI step; else fail (unknown if the CI extractor failed)."""
    hits: list[tuple[str, Evidence | None]] = []  # (description, evidence)
    candidates = [f.path for f in ctx.files if f.readable and not f.is_vendored]
    for path in candidates:
        if glob_match(path, COVERAGE_FILES):
            hits.append((path, ctx.evidence(path, 1)))
    read = 0
    for path in candidates:
        pattern = next((p for g, p in COVERAGE_CONTENT if glob_match(path, g)), None)
        if pattern is None or read >= MAX_CONFIG_FILES:
            continue
        read += 1
        text = ctx.read_text(path)
        match = pattern.search(text) if text else None
        if text and match:
            hits.append((path, ctx.evidence(path, text.count("\n", 0, match.start()) + 1)))
    for step in ctx.facts.by_kind("ci_step"):
        if COVERAGE_STEP.search(f"{attr_str(step, 'run') or ''}\n{attr_str(step, 'uses') or ''}"):
            hits.append((f"CI step in {attr_str(step, 'path')}", next(iter(step.evidence), None)))
    query = "coverage config files, coverage settings, CI coverage flags"
    if hits:
        evidence: list[Evidence] = [scan(ctx, CI_TOOL, query, len(hits))]
        evidence += [e for _, e in hits[: MAX_EVIDENCE - 1] if e is not None]
        return RuleOutcome(
            verdict="pass",
            claim=f"Coverage is configured: {', '.join(sorted({d for d, _ in hits})[:3])}.",
            evidence=evidence,
        )
    run = ctx.tool_run(CI_TOOL)
    if run is None or run.status in ("error", "timeout"):
        return RuleOutcome(
            verdict="unknown",
            claim="No coverage configuration found, but CI steps could not be inspected.",
            evidence=[],
            reason=f"tool_{run.status}" if run else "tool_not_run",
        )
    return RuleOutcome(
        verdict="fail",
        claim="No coverage configuration, coverage settings or CI coverage flags found.",
        evidence=[scan(ctx, CI_TOOL, query, 0)],
    )
