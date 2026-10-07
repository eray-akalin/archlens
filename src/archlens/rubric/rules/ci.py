"""`ci.*` rules over `ci_workflow`, `ci_step` and `actionlint_finding` facts (CI-01…05, TEST-03).

A step counts for every stage its command matches (the extractor's single `run_kind` keeps only
the highest-priority one, so `ruff check . && pytest` would otherwise hide the lint stage).
"""

import re
from typing import Literal

from pydantic import Field

from archlens.facts.extractors.ci import RUN_KINDS
from archlens.models import Evidence, Fact, RuleOutcome
from archlens.rubric.context import RuleContext
from archlens.rubric.registry import RuleParams, rule
from archlens.rubric.rules._common import (
    MAX_EVIDENCE,
    attr_bool,
    attr_dict,
    attr_list,
    attr_str,
    fact_evidence,
    line_evidence,
    missing_data,
    plural,
    scan,
)

CI_TOOL = "ast:ci"
ACTIONLINT = "actionlint"
GITHUB = "github_actions"

Stage = Literal["build", "lint", "test", "deploy"]


def step_kinds(step: Fact) -> set[str]:
    """`run_kind` plus every other kind whose pattern matches the step's command or action."""
    haystack = f"{attr_str(step, 'run') or ''}\n{attr_str(step, 'uses') or ''}"
    kinds = {kind for kind, pattern in RUN_KINDS if pattern.search(haystack)}
    return kinds | {attr_str(step, "run_kind") or "other"}


def _no_workflows(ctx: RuleContext, system: str | None = None) -> RuleOutcome | None:
    """unknown/NA per RUBRICS §5 when the CI extractor failed or found no (GitHub) workflows."""
    if (missing := missing_data(ctx, CI_TOOL)) is not None:
        return missing
    workflows = _workflows(ctx, system)
    if workflows:
        return None
    label = "GitHub Actions workflows" if system == GITHUB else "CI workflows"
    return RuleOutcome(
        verdict="not_applicable",
        claim=f"No {label} found.",
        evidence=[scan(ctx, CI_TOOL, label, 0)],
        reason="no_github_workflows" if system == GITHUB else "no_ci",
    )


def _workflows(ctx: RuleContext, system: str | None = None) -> list[Fact]:
    return [
        f
        for f in ctx.facts.by_kind("ci_workflow")
        if system is None or attr_str(f, "system") == system
    ]


def _steps(ctx: RuleContext, system: str | None = None) -> list[Fact]:
    return [
        f for f in ctx.facts.by_kind("ci_step") if system is None or attr_str(f, "system") == system
    ]


class PresentParams(RuleParams):
    pass


@rule("ci.present")
def present(ctx: RuleContext, params: PresentParams) -> RuleOutcome:
    """pass if any `ci_workflow` fact exists, else fail."""
    if (missing := missing_data(ctx, CI_TOOL)) is not None:
        return missing
    workflows = _workflows(ctx)
    evidence: list[Evidence] = [scan(ctx, CI_TOOL, "CI workflows", len(workflows))]
    if not workflows:
        return RuleOutcome(verdict="fail", claim="No CI configuration found.", evidence=evidence)
    systems = sorted({attr_str(f, "system") or "?" for f in workflows})
    paths = [attr_str(f, "path") or "?" for f in workflows]
    return RuleOutcome(
        verdict="pass",
        claim=f"{plural(len(workflows), 'CI workflow')} ({', '.join(systems)}): "
        f"{', '.join(paths[:3])}.",
        evidence=evidence + fact_evidence(workflows, MAX_EVIDENCE - 1),
    )


class StagesParams(RuleParams):
    required: list[Stage] = Field(default_factory=lambda: ["build", "lint", "test"], min_length=1)


@rule("ci.stages")
def stages(ctx: RuleContext, params: StagesParams) -> RuleOutcome:
    """All `required` stages present → pass, at least one → partial, none → fail."""
    if (outcome := _no_workflows(ctx)) is not None:
        return outcome
    steps = _steps(ctx)
    first: dict[str, Fact] = {}
    for step in steps:
        for kind in step_kinds(step):
            first.setdefault(kind, step)
    found = [s for s in params.required if s in first]
    missing = [s for s in params.required if s not in first]
    evidence: list[Evidence] = [
        scan(ctx, CI_TOOL, f"CI stages {params.required}", len(found)),
        *fact_evidence([first[s] for s in found]),
    ]
    if not missing:
        return RuleOutcome(
            verdict="pass", claim=f"CI runs {', '.join(found)} steps.", evidence=evidence
        )
    if found:
        return RuleOutcome(
            verdict="partial",
            claim=f"CI runs {', '.join(found)} but no {', '.join(missing)} step.",
            evidence=evidence,
        )
    return RuleOutcome(
        verdict="fail",
        claim=f"CI runs none of the stages {', '.join(params.required)}.",
        evidence=evidence + fact_evidence(_workflows(ctx), MAX_EVIDENCE - 1),
    )


class RunsTestsParams(RuleParams):
    pass


@rule("ci.runs_tests")
def runs_tests(ctx: RuleContext, params: RunsTestsParams) -> RuleOutcome:
    """pass if a CI step runs tests, else fail; NA without CI."""
    if (outcome := _no_workflows(ctx)) is not None:
        return outcome
    tests = [s for s in _steps(ctx) if "test" in step_kinds(s)]
    evidence: list[Evidence] = [scan(ctx, CI_TOOL, "CI steps that run tests", len(tests))]
    if tests:
        commands = [
            (attr_str(s, "run") or attr_str(s, "uses") or "?").splitlines()[0] for s in tests
        ]
        return RuleOutcome(
            verdict="pass",
            claim=f"{plural(len(tests), 'CI step')} run tests: {', '.join(commands[:3])}.",
            evidence=evidence + fact_evidence(tests, MAX_EVIDENCE - 1),
        )
    return RuleOutcome(
        verdict="fail",
        claim="No CI step runs the test suite.",
        evidence=evidence + fact_evidence(_workflows(ctx), MAX_EVIDENCE - 1),
    )


class ActionlintCleanParams(RuleParams):
    max_errors: int = Field(default=0, ge=0)


@rule("ci.actionlint_clean")
def actionlint_clean(ctx: RuleContext, params: ActionlintCleanParams) -> RuleOutcome:
    """pass if actionlint reported at most `max_errors` problems; NA without GitHub workflows."""
    if (missing := missing_data(ctx, ACTIONLINT)) is not None:
        return missing
    findings = ctx.facts.by_kind("actionlint_finding")
    evidence: list[Evidence] = [scan(ctx, ACTIONLINT, "workflow errors", len(findings))]
    if len(findings) <= params.max_errors:
        return RuleOutcome(
            verdict="pass",
            claim=f"actionlint reported {plural(len(findings), 'error')} "
            f"(allowed: {params.max_errors}).",
            evidence=evidence,
        )
    kinds = sorted({attr_str(f, "kind") or "?" for f in findings})
    return RuleOutcome(
        verdict="fail",
        claim=f"actionlint reported {plural(len(findings), 'error')} ({', '.join(kinds[:3])}); "
        f"allowed: {params.max_errors}.",
        evidence=evidence + fact_evidence(findings, MAX_EVIDENCE - 1),
    )


class PermissionsParams(RuleParams):
    pass


def is_restricted(workflow: Fact) -> bool:
    """Top-level or every-job `permissions` set and not `write-all`; a job-level `write-all`
    escalates past a restrictive top level, so it never counts as restricted."""
    jobs = [str(j) for j in attr_list(workflow, "jobs")]
    per_job = attr_dict(workflow, "job_permissions")
    if any(per_job.get(j) == "write-all" for j in jobs):
        return False
    top = workflow.attributes.get("permissions")
    if top is not None and top != "write-all":
        return True
    return bool(jobs) and all(per_job.get(j) is not None for j in jobs)


_WRITE_ALL = re.compile(r"^\s*permissions:\s*['\"]?write-all")


@rule("ci.permissions_restricted")
def permissions_restricted(ctx: RuleContext, params: PermissionsParams) -> RuleOutcome:
    """pass if every GitHub workflow restricts token permissions, else fail."""
    if (outcome := _no_workflows(ctx, GITHUB)) is not None:
        return outcome
    workflows = _workflows(ctx, GITHUB)
    open_ = [w for w in workflows if not is_restricted(w)]
    query = "workflows without restricted `permissions`"
    if not open_:
        return RuleOutcome(
            verdict="pass",
            claim=f"All {plural(len(workflows), 'GitHub workflow')} restrict token permissions.",
            evidence=[scan(ctx, CI_TOOL, query, 0), *fact_evidence(workflows, MAX_EVIDENCE - 1)],
        )
    paths = [attr_str(w, "path") or "?" for w in open_]
    return RuleOutcome(
        verdict="fail",
        claim=f"Token permissions are default or write-all in {len(open_)} of "
        f"{plural(len(workflows), 'GitHub workflow')}: {', '.join(paths[:3])}.",
        evidence=[
            scan(ctx, CI_TOOL, query, len(open_)),
            *line_evidence(ctx, [(p, _WRITE_ALL) for p in paths], open_)[: MAX_EVIDENCE - 1],
        ],
    )


class ActionsPinnedParams(RuleParams):
    allow_owners: list[str] = Field(default_factory=lambda: ["actions", "github"])


@rule("ci.actions_pinned")
def actions_pinned(ctx: RuleContext, params: ActionsPinnedParams) -> RuleOutcome:
    """pass if every third-party `uses:` (owner not in `allow_owners`) is pinned to a 40-hex SHA."""
    if (outcome := _no_workflows(ctx, GITHUB)) is not None:
        return outcome
    allowed = {o.lower() for o in params.allow_owners}
    third_party = [
        s
        for s in _steps(ctx, GITHUB)
        if (uses := attr_str(s, "uses")) and uses.split("/", 1)[0].lower() not in allowed
    ]
    unpinned = [s for s in third_party if not attr_bool(s, "pinned")]
    query = "third-party actions not pinned to a commit SHA"
    if not unpinned:
        refs = plural(len(third_party), "third-party action reference")
        return RuleOutcome(
            verdict="pass",
            claim=f"All {refs} are pinned to a commit SHA."
            if third_party
            else "No third-party actions are used.",
            evidence=[scan(ctx, CI_TOOL, query, 0), *fact_evidence(third_party, MAX_EVIDENCE - 1)],
        )
    refs = sorted({attr_str(s, "uses") or "?" for s in unpinned})
    return RuleOutcome(
        verdict="fail",
        claim=f"Not pinned to a commit SHA: {len(unpinned)} of "
        f"{plural(len(third_party), 'third-party action reference')} ({', '.join(refs[:3])}).",
        evidence=[
            scan(ctx, CI_TOOL, query, len(unpinned)),
            *fact_evidence(unpinned, MAX_EVIDENCE - 1),
        ],
    )
