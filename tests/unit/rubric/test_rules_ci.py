"""ci.present, ci.stages, ci.runs_tests, ci.actionlint_clean, ci.permissions_restricted,
ci.actions_pinned."""

from pathlib import Path

import pytest
from pydantic import JsonValue

from archlens.models import Fact, ToolStatus
from tests.unit.rubric.helpers import ci_step, ci_workflow, fact, first_scan, make_ctx, run

GITLAB = ".gitlab-ci.yml"
SHA = "0123456789abcdef0123456789abcdef01234567"
CI_RULES = [
    "ci.present",
    "ci.stages",
    "ci.runs_tests",
    "ci.permissions_restricted",
    "ci.actions_pinned",
]


@pytest.mark.parametrize("status", ["error", "timeout", None])
@pytest.mark.parametrize("name", CI_RULES)
def test_ci_extractor_failure_is_unknown(
    name: str, status: ToolStatus | None, tmp_path: Path
) -> None:
    outcome = run(name, make_ctx(tmp_path, [ci_workflow()], status={"ast:ci": status}))
    assert outcome.verdict == "unknown"
    assert outcome.reason == (f"tool_{status}" if status else "tool_not_run")


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("ci.stages", "no_ci"),
        ("ci.runs_tests", "no_ci"),
        ("ci.permissions_restricted", "no_github_workflows"),
        ("ci.actions_pinned", "no_github_workflows"),
    ],
)
def test_no_workflows_is_not_applicable(name: str, reason: str, tmp_path: Path) -> None:
    outcome = run(name, make_ctx(tmp_path))
    assert (outcome.verdict, outcome.reason) == ("not_applicable", reason)


@pytest.mark.parametrize("name", ["ci.permissions_restricted", "ci.actions_pinned"])
def test_github_only_rules_are_not_applicable_for_other_ci(name: str, tmp_path: Path) -> None:
    facts = [
        ci_workflow(GITLAB, system="gitlab_ci"),
        ci_step(run="pytest", path=GITLAB, system="gitlab_ci"),
    ]
    outcome = run(name, make_ctx(tmp_path, facts))
    assert (outcome.verdict, outcome.reason) == ("not_applicable", "no_github_workflows")


# --- ci.present --------------------------------------------------------------------------------


def test_present_pass_and_fail(tmp_path: Path) -> None:
    workflow = ci_workflow()
    passed = run("ci.present", make_ctx(tmp_path / "a", [workflow]))
    assert passed.verdict == "pass"
    assert workflow.evidence[0] in passed.evidence
    failed = run("ci.present", make_ctx(tmp_path / "b"))
    assert failed.verdict == "fail"
    assert first_scan(failed).result_count == 0


# --- ci.stages ---------------------------------------------------------------------------------


def stages_of(tmp_path: Path, *steps: tuple[str, str], **params: JsonValue) -> str:
    facts = [ci_workflow(), *(ci_step(run=cmd, run_kind=kind) for cmd, kind in steps)]
    return run("ci.stages", make_ctx(tmp_path, facts), **params).verdict


def test_stages_all_present(tmp_path: Path) -> None:
    steps = [("uv build", "build"), ("ruff check .", "lint"), ("pytest", "test")]
    assert stages_of(tmp_path, *steps) == "pass"


def test_stages_partial_and_fail(tmp_path: Path) -> None:
    assert (
        stages_of(tmp_path / "a", ("ruff check .", "lint"), ("docker build .", "build"))
        == "partial"
    )
    assert stages_of(tmp_path / "b", ("echo hi", "other")) == "fail"


def test_stages_counts_every_kind_a_step_matches(tmp_path: Path) -> None:
    # run_kind keeps only "test" (deploy > test > lint > build), but the step also lints and builds
    assert stages_of(tmp_path, ("ruff check . && uv build && pytest", "test")) == "pass"


def test_stages_required_param(tmp_path: Path) -> None:
    assert stages_of(tmp_path, ("pytest", "test"), required=["test"]) == "pass"


def test_stages_partial_claim_names_the_gap(tmp_path: Path) -> None:
    facts = [ci_workflow(), ci_step(run="ruff check .", run_kind="lint")]
    outcome = run("ci.stages", make_ctx(tmp_path, facts))
    assert outcome.claim == "CI runs lint but no build, test step."


# --- ci.runs_tests -----------------------------------------------------------------------------


def test_runs_tests_pass(tmp_path: Path) -> None:
    step = ci_step(run="pytest -q\necho done", run_kind="test")
    outcome = run("ci.runs_tests", make_ctx(tmp_path, [ci_workflow(), step]))
    assert outcome.verdict == "pass"
    assert "pytest -q" in outcome.claim and "echo done" not in outcome.claim
    assert step.evidence[0] in outcome.evidence


def test_runs_tests_in_a_deploy_step(tmp_path: Path) -> None:
    step = ci_step(run="pytest && docker push img", run_kind="deploy")
    assert run("ci.runs_tests", make_ctx(tmp_path, [ci_workflow(), step])).verdict == "pass"


def test_runs_tests_fail(tmp_path: Path) -> None:
    facts = [ci_workflow(), ci_step(run="ruff check .", run_kind="lint")]
    outcome = run("ci.runs_tests", make_ctx(tmp_path, facts))
    assert outcome.verdict == "fail"


# --- ci.actionlint_clean -----------------------------------------------------------------------


def lint_error() -> Fact:
    return fact("actionlint_finding", kind="expression", message="undefined property")


def test_actionlint_clean(tmp_path: Path) -> None:
    assert run("ci.actionlint_clean", make_ctx(tmp_path / "a")).verdict == "pass"
    ctx = make_ctx(tmp_path / "b", [lint_error()])
    failed = run("ci.actionlint_clean", ctx)
    assert failed.verdict == "fail" and "expression" in failed.claim
    assert run("ci.actionlint_clean", ctx, max_errors=1).verdict == "pass"


def test_actionlint_skipped_is_not_applicable(tmp_path: Path) -> None:
    outcome = run("ci.actionlint_clean", make_ctx(tmp_path, status={"actionlint": "skipped"}))
    assert (outcome.verdict, outcome.reason) == ("not_applicable", "tool_skipped")


@pytest.mark.parametrize("status", ["error", "timeout", None])
def test_actionlint_failure_is_unknown(status: ToolStatus | None, tmp_path: Path) -> None:
    outcome = run("ci.actionlint_clean", make_ctx(tmp_path, status={"actionlint": status}))
    assert outcome.verdict == "unknown"


# --- ci.permissions_restricted -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("permissions", "job_permissions", "expected"),
    [
        ({"contents": "read"}, {"build": None, "deploy": None}, "pass"),
        ("read-all", {"build": None, "deploy": None}, "pass"),
        ({}, {"build": None, "deploy": None}, "pass"),  # `permissions: {}` = no scopes at all
        (None, {"build": {"contents": "read"}, "deploy": {"packages": "write"}}, "pass"),
        (None, {"build": None, "deploy": None}, "fail"),
        ("write-all", {"build": None, "deploy": None}, "fail"),
        (None, {"build": {"contents": "read"}, "deploy": None}, "fail"),
        ({"contents": "read"}, {"build": None, "deploy": "write-all"}, "fail"),
    ],
)
def test_permissions(
    permissions: JsonValue, job_permissions: JsonValue, expected: str, tmp_path: Path
) -> None:
    workflow = ci_workflow(
        permissions=permissions, jobs=["build", "deploy"], job_permissions=job_permissions
    )
    assert run("ci.permissions_restricted", make_ctx(tmp_path, [workflow])).verdict == expected


def test_permissions_fail_names_every_open_workflow(tmp_path: Path) -> None:
    ok = ci_workflow(".github/workflows/a.yml", permissions={"contents": "read"})
    open_ = ci_workflow(".github/workflows/b.yml")
    outcome = run("ci.permissions_restricted", make_ctx(tmp_path, [ok, open_]))
    assert outcome.verdict == "fail"
    assert "1 of 2 GitHub workflows" in outcome.claim and "b.yml" in outcome.claim
    assert open_.evidence[0] in outcome.evidence and ok.evidence[0] not in outcome.evidence


# --- ci.actions_pinned -------------------------------------------------------------------------


def pinned_of(tmp_path: Path, *uses: str, **params: JsonValue) -> str:
    steps = [
        ci_step(uses=u, pinned=u.startswith(("./", "docker://")) or u.endswith(SHA)) for u in uses
    ]
    return run("ci.actions_pinned", make_ctx(tmp_path, [ci_workflow(), *steps]), **params).verdict


def test_actions_pinned_pass(tmp_path: Path) -> None:
    uses = ["actions/checkout@v4", "github/codeql-action/init@v3", f"docker/login-action@{SHA}"]
    assert (
        pinned_of(tmp_path / "a", *uses, "./.github/actions/setup", "docker://alpine:3") == "pass"
    )
    assert pinned_of(tmp_path / "b", "actions/checkout@v4") == "pass"  # no third-party actions


def test_actions_pinned_fail(tmp_path: Path) -> None:
    facts = [
        ci_workflow(),
        ci_step(uses="docker/login-action@v3", pinned=False),
        ci_step(uses=f"x/y@{SHA}", pinned=True),
    ]
    outcome = run("ci.actions_pinned", make_ctx(tmp_path, facts))
    assert outcome.verdict == "fail"
    assert outcome.claim == (
        "Not pinned to a commit SHA: 1 of 2 third-party action references (docker/login-action@v3)."
    )


def test_actions_pinned_allow_owners(tmp_path: Path) -> None:
    assert pinned_of(tmp_path / "a", "docker/login-action@v3", allow_owners=["docker"]) == "pass"
    assert pinned_of(tmp_path / "b", "actions/checkout@v4", allow_owners=[]) == "fail"
