"""data.*, ast.*, observability.*, docker.*, iac.*, deploy.*, docs.* rules."""

from pathlib import Path

import pytest
from pydantic import JsonValue

from archlens.models import Fact, Severity, ToolStatus
from archlens.rubric.rules.docker import is_pinned_image, runs_as_non_root
from tests.unit.rubric.helpers import fact, first_scan, make_ctx, run


def dockerfile(path: str = "Dockerfile", *, user: str | None = "app", stages: int = 2,
               bases: list[JsonValue] | None = None, healthcheck: bool = False) -> Fact:  # fmt: skip
    external: list[JsonValue] = bases if bases is not None else ["python:3.12-slim"]
    return fact(
        "dockerfile", path=path, base_images=external, external_bases=external, stages=stages,
        stage_aliases=[], final_base=external[-1] if external else None, user=user,
        has_healthcheck=healthcheck,
    )  # fmt: skip


def workload(
    name: str, *, limits: bool = False, health: bool = False, kind: str = "compose_service"
) -> Fact:
    return fact(
        "deploy_config", path="compose.yml", kind=kind, workload=None, name=name,
        has_limits=limits, has_liveness=False, has_readiness=False, has_healthcheck=health,
    )  # fmt: skip


def output(kind: str, path: str = "app/main.py", in_test: bool = False) -> Fact:
    return fact(kind, path=path, in_test=in_test, call="print", logger="log", level="info")


TOOL_RULES = [
    ("ast.debug_output_ratio", "ast:logging"),
    ("docker.non_root", "ast:docker"),
    ("docker.pinned_base", "ast:docker"),
    ("docker.multistage", "ast:docker"),
    ("docker.healthcheck", "ast:docker"),
    ("docker.hadolint_errors", "hadolint"),
    ("iac.max_severity", "checkov"),
    ("deploy.resource_limits", "ast:deploy"),
    ("docs.docstring_ratio", "ast:metrics"),
]


@pytest.mark.parametrize("status", ["error", "timeout", None])
@pytest.mark.parametrize(("name", "tool"), TOOL_RULES)
def test_tool_failure_is_unknown(
    name: str, tool: str, status: ToolStatus | None, tmp_path: Path
) -> None:
    assert run(name, make_ctx(tmp_path, [dockerfile()], status={tool: status})).verdict == "unknown"


def test_observability_unknown_when_both_extractors_failed(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, status={"ast:imports": "error", "ast:routes": None})
    assert run("observability.present", ctx).verdict == "unknown"


# --- data.migrations_present -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "verdict"),
    [("backend/app/alembic/versions/001_init.py", "pass"), ("shop/migrations/0001_initial.py", "pass"),
     ("Data/Migrations/20240101_Init.cs", "pass"), ("prisma/migrations/x/migration.sql", "pass"),
     ("app/db.py", "fail")],
)  # fmt: skip
def test_migrations(path: str, verdict: str, tmp_path: Path) -> None:
    outcome = run("data.migrations_present", make_ctx(tmp_path, files={path: "x = 1\n"}))
    assert outcome.verdict == verdict
    if verdict == "fail":
        assert outcome.claim == "No schema migrations found."


# --- ast.debug_output_ratio --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prints", "logs", "verdict"), [(0, 30, "pass"), (1, 9, "partial"), (3, 0, "fail")]
)
def test_debug_output(prints: int, logs: int, verdict: str, tmp_path: Path) -> None:
    facts = [output("print_call") for _ in range(prints)] + [
        output("log_call") for _ in range(logs)
    ]
    facts = [f.model_copy(update={"id": f"{f.id}{i}"}) for i, f in enumerate(facts)]
    assert run("ast.debug_output_ratio", make_ctx(tmp_path, facts)).verdict == verdict


def test_debug_output_ignores_tests_and_needs_output(tmp_path: Path) -> None:
    only_tests = [output("print_call", "tests/test_x.py", in_test=True)]
    outcome = run("ast.debug_output_ratio", make_ctx(tmp_path, only_tests))
    assert (outcome.verdict, outcome.reason) == ("not_applicable", "no_output_calls")


# --- observability.present ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("facts", "verdict"),
    [
        ([fact("route", path="/health", method="GET", file="app/main.py")], "pass"),
        ([fact("route", path="/api/v1/utils/health-check", method="GET", file="a.py")], "pass"),
        ([fact("route", path="/metrics", method="GET", file="a.py")], "pass"),
        (
            [
                fact(
                    "import_edge",
                    from_module="app",
                    to_module="opentelemetry.trace",
                    internal=False,
                )
            ],
            "pass",
        ),
        (
            [
                fact(
                    "import_edge", from_module="app", to_module="@opentelemetry/api", internal=False
                )
            ],
            "pass",
        ),
        ([fact("route", path="/users", method="GET", file="a.py")], "fail"),
        ([], "fail"),
    ],
)
def test_observability(facts: list[Fact], verdict: str, tmp_path: Path) -> None:
    assert run("observability.present", make_ctx(tmp_path, facts)).verdict == verdict


# --- docker.* ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("user", "ok"),
    [
        ("app", True),
        ("1000", True),
        ("app:app", True),
        ("root", False),
        ("0:0", False),
        (None, False),
    ],
)
def test_non_root_users(user: str | None, ok: bool) -> None:
    assert runs_as_non_root(dockerfile(user=user)) is ok


def test_non_root_aggregation(tmp_path: Path) -> None:
    good, bad = dockerfile("api/Dockerfile"), dockerfile("web/Dockerfile", user=None)
    assert run("docker.non_root", make_ctx(tmp_path / "a", [good])).verdict == "pass"
    mixed = run("docker.non_root", make_ctx(tmp_path / "b", [good, bad]))
    assert mixed.verdict == "partial" and "web/Dockerfile" in mixed.claim
    assert run("docker.non_root", make_ctx(tmp_path / "c", [bad])).verdict == "fail"
    assert run("docker.non_root", make_ctx(tmp_path / "d")).verdict == "not_applicable"


@pytest.mark.parametrize(
    ("image", "pinned"),
    [("python:3.12-slim", True), ("python@sha256:abc", True), ("ghcr.io/o/i:1.2", True),
     ("localhost:5000/img:2", True), ("${BASE_IMAGE}", True), ("scratch", True),
     ("python", False), ("python:latest", False), ("localhost:5000/img", False)],
)  # fmt: skip
def test_pinned_images(image: str, pinned: bool) -> None:
    assert is_pinned_image(image) is pinned


def test_pinned_base_rule(tmp_path: Path) -> None:
    assert run("docker.pinned_base", make_ctx(tmp_path / "a", [dockerfile()])).verdict == "pass"
    latest = dockerfile(bases=["node:20", "python:latest"])
    outcome = run("docker.pinned_base", make_ctx(tmp_path / "b", [latest]))
    assert outcome.verdict == "fail" and "python:latest" in outcome.claim


def test_multistage(tmp_path: Path) -> None:
    assert (
        run("docker.multistage", make_ctx(tmp_path / "a", [dockerfile(stages=3)])).verdict == "pass"
    )
    assert (
        run("docker.multistage", make_ctx(tmp_path / "b", [dockerfile(stages=1)])).verdict == "fail"
    )


@pytest.mark.parametrize(
    ("facts", "verdict"),
    [
        ([dockerfile(healthcheck=True)], "pass"),
        ([dockerfile(), workload("api", health=True)], "pass"),
        ([fact("deploy_config", path="k.yml", kind="k8s_workload", workload="Deployment", name="api",
               has_limits=True, has_liveness=True, has_readiness=False, has_healthcheck=False)], "pass"),
        ([dockerfile(), workload("api")], "fail"),
        ([], "not_applicable"),
    ],
)  # fmt: skip
def test_healthcheck(facts: list[Fact], verdict: str, tmp_path: Path) -> None:
    assert run("docker.healthcheck", make_ctx(tmp_path, facts)).verdict == verdict


@pytest.mark.parametrize(
    ("severities", "verdict"), [([], "pass"), (["medium", "low"], "pass"), (["high"], "fail")]
)
def test_hadolint(severities: list[Severity], verdict: str, tmp_path: Path) -> None:
    facts = [
        fact("hadolint_finding", s, code=f"DL{i}", level="x", message="m")
        for i, s in enumerate(severities)
    ]
    assert run("docker.hadolint_errors", make_ctx(tmp_path, facts)).verdict == verdict


def test_hadolint_skipped_and_allowance(tmp_path: Path) -> None:
    skipped = make_ctx(tmp_path / "a", status={"hadolint": "skipped"})
    assert run("docker.hadolint_errors", skipped).verdict == "not_applicable"
    one = make_ctx(
        tmp_path / "b",
        [fact("hadolint_finding", "high", code="DL4000", level="error", message="m")],
    )
    assert run("docker.hadolint_errors", one, max_errors=1).verdict == "pass"


# --- iac.max_severity --------------------------------------------------------------------------


def iac(severity: Severity | None, i: int = 0) -> Fact:
    return fact(
        "iac_finding",
        severity,
        check_id=f"CKV_{i}",
        check_name="n",
        resource="r",
        framework="terraform",
    )


@pytest.mark.parametrize(
    ("findings", "verdict"),
    [([], "pass"), ([iac("medium")], "partial"), ([iac(None, i) for i in range(4)], "partial"),
     ([iac(None, i) for i in range(5)], "fail"), ([iac("high")], "fail"), ([iac("critical")], "fail")],
)  # fmt: skip
def test_iac(findings: list[Fact], verdict: str, tmp_path: Path) -> None:
    assert run("iac.max_severity", make_ctx(tmp_path, findings)).verdict == verdict


def test_iac_skipped(tmp_path: Path) -> None:
    outcome = run("iac.max_severity", make_ctx(tmp_path, status={"checkov": "skipped"}))
    assert (outcome.verdict, outcome.reason) == ("not_applicable", "tool_skipped")


# --- deploy.resource_limits --------------------------------------------------------------------


def test_resource_limits(tmp_path: Path) -> None:
    assert (
        run(
            "deploy.resource_limits", make_ctx(tmp_path / "a", [workload("a", limits=True)])
        ).verdict
        == "pass"
    )
    mixed = [workload("a", limits=True), workload("b")]
    assert run("deploy.resource_limits", make_ctx(tmp_path / "b", mixed)).verdict == "partial"
    assert (
        run("deploy.resource_limits", make_ctx(tmp_path / "c", [workload("b")])).verdict == "fail"
    )
    assert run("deploy.resource_limits", make_ctx(tmp_path / "d")).verdict == "not_applicable"


# --- docs.* ------------------------------------------------------------------------------------


def test_api_spec(tmp_path: Path) -> None:
    spec = make_ctx(tmp_path / "a", files={"docs/openapi.yaml": "openapi: 3.1.0\n"})
    outcome = run("docs.api_spec", spec)
    assert outcome.verdict == "pass" and "docs/openapi.yaml" in outcome.claim
    fastapi = make_ctx(tmp_path / "b", frameworks=["fastapi"])
    assert run("docs.api_spec", fastapi).claim == "API docs are generated by fastapi."
    swagger = make_ctx(
        tmp_path / "c",
        [
            fact(
                "dependency",
                name="Swashbuckle.AspNetCore",
                version_spec="6",
                dev=False,
                manifest="Api.csproj",
            )
        ],
    )
    assert run("docs.api_spec", swagger).verdict == "pass"
    assert run("docs.api_spec", make_ctx(tmp_path / "d", frameworks=["flask"])).verdict == "fail"


@pytest.mark.parametrize(
    ("path", "verdict"),
    [("ARCHITECTURE.md", "pass"), ("docs/adr/0001-record.md", "pass"), ("docs/decisions/x.md", "pass"),
     ("docs/architecture.md", "pass"), ("README.md", "fail")],
)  # fmt: skip
def test_architecture_docs(path: str, verdict: str, tmp_path: Path) -> None:
    assert run("docs.architecture", make_ctx(tmp_path, files={path: "# x\n"})).verdict == verdict


@pytest.mark.parametrize(("documented", "verdict"), [(7, "pass"), (4, "partial"), (1, "fail")])
def test_docstring_ratio(documented: int, verdict: str, tmp_path: Path) -> None:
    files = [
        fact("file_metrics", evidence=[], path="a.py", loc=100, is_test=False, is_generated=False,
             public_functions=10, documented_functions=documented),
        fact("file_metrics", evidence=[], path="tests/t.py", loc=50, is_test=True, is_generated=False,
             public_functions=10, documented_functions=0),
    ]  # fmt: skip
    outcome = run("docs.docstring_ratio", make_ctx(tmp_path, files))
    assert (
        outcome.verdict == verdict
        and first_scan(outcome).query == f"documented {documented} / public 10 functions"
    )


def test_docstring_ratio_without_public_functions(tmp_path: Path) -> None:
    assert run("docs.docstring_ratio", make_ctx(tmp_path)).verdict == "not_applicable"
