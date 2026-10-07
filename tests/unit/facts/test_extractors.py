"""Per-extractor tests on small hand-written repos, plus tiny_service expectations from DEFECTS.md."""

from pathlib import Path

import pytest

from archlens.config import ToolsConfig
from archlens.facts import extractors as extractors_module
from archlens.facts.base import ScanContext
from archlens.facts.extractors import run_extractors
from archlens.facts.extractors.ci import classify_step, extract_ci, is_pinned
from archlens.facts.extractors.deploy import extract_deploy_configs
from archlens.facts.extractors.docker import extract_dockerfiles, parse_dockerfile
from archlens.facts.extractors.docs import doc_type
from archlens.facts.extractors.imports import extract_imports, resolve_relative
from archlens.facts.extractors.logging_ import extract_log_calls
from archlens.facts.extractors.manifests import extract_manifests
from archlens.facts.extractors.metrics import extract_file_metrics
from archlens.facts.extractors.routes import extract_routes
from archlens.facts.extractors.tests import extract_test_files
from archlens.ingest.snapshot import build_snapshot
from archlens.models import CodeEvidence, Fact, IngestLimits
from tests.fixture_repos import MaterializedRepo


def repo(tmp_path: Path, files: dict[str, str]) -> ScanContext:
    root = tmp_path / "repo"
    for rel, content in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(content)
    snapshot = build_snapshot(root, limits=IngestLimits())
    return ScanContext.create(
        root, snapshot, tmp_path / "work", ToolsConfig(tools={}), tmp_path / "tools"
    )


def attrs(facts: list[Fact], kind: str) -> list[dict[str, object]]:
    return [dict(f.attributes) for f in facts if f.kind == kind]


def line(fact: Fact) -> int:
    evidence = fact.evidence[0]
    assert isinstance(evidence, CodeEvidence)
    return evidence.start_line


def tiny(tiny_service: MaterializedRepo, tmp_path: Path) -> ScanContext:
    snapshot = build_snapshot(tiny_service.root, limits=IngestLimits())
    return ScanContext.create(
        tiny_service.root, snapshot, tmp_path / "work", ToolsConfig(tools={}), tmp_path / "t"
    )


# --- imports ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("here", "package", "module", "level", "expected"),
    [
        ("app.api.users", False, "db", 2, "app.db"),
        ("app.api.users", False, None, 1, "app.api"),
        ("app", True, "models", 1, "app.models"),
        ("app.x", False, "y", 0, "y"),
        ("a", False, "b", 3, None),
    ],
)
def test_resolve_relative(
    here: str, package: bool, module: str | None, level: int, expected: str | None
) -> None:
    assert resolve_relative(here, package, module, level) == expected


def test_python_imports(tmp_path: Path) -> None:
    ctx = repo(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/sub.py": "import os\nfrom . import helpers\nfrom .helpers import x\n",
            "pkg/helpers.py": "import requests\n",
        },
    )
    edges = {
        (a["from_module"], a["to_module"], a["internal"])
        for a in attrs(extract_imports(ctx), "import_edge")
    }
    assert ("pkg.sub", "os", False) in edges
    assert ("pkg.sub", "pkg.helpers", True) in edges
    assert ("pkg.helpers", "requests", False) in edges


def test_js_imports(tmp_path: Path) -> None:
    source = "import a from './lib/a'\nconst b = require('lodash/fp')\nimport { c } from '@scope/pkg/x'\nimport d from '@/utils'\n"
    ctx = repo(tmp_path, {"src/app.ts": source, "src/lib/a.ts": "export const a = 1\n"})
    edges = {(a["to_module"], a["internal"]) for a in attrs(extract_imports(ctx), "import_edge")}
    assert edges == {
        ("src/lib/a", True),
        ("lodash", False),
        ("@scope/pkg", False),
        ("@/utils", True),
    }


def test_tiny_service_cycle(tiny_service: MaterializedRepo, tmp_path: Path) -> None:
    edges = {
        (a["from_module"], a["to_module"])
        for a in attrs(extract_imports(tiny(tiny_service, tmp_path)), "import_edge")
    }
    assert {
        ("app.services.billing", "app.services.notify"),
        ("app.services.notify", "app.services.billing"),
    } <= edges


# --- manifests ----------------------------------------------------------------------------------


def test_pyproject_and_requirements(tmp_path: Path) -> None:
    pyproject = (
        '[project]\ndependencies = ["fastapi>=0.110", "pydantic[email]>=2 ; python_version>\'3.10\'"]\n'
        '[project.optional-dependencies]\ndev = ["pytest>=8"]\n[dependency-groups]\nlint = ["ruff"]\n'
    )
    ctx = repo(
        tmp_path,
        {"pyproject.toml": pyproject, "requirements.txt": "flask==3.0.0\n# c\n-r base.txt\n"},
    )
    facts = extract_manifests(ctx)
    manifests = {a["type"]: a for a in attrs(facts, "manifest")}
    assert manifests["pyproject"]["has_lockfile"] is False
    assert manifests["requirements"]["has_lockfile"] is True  # every dependency pinned with ==
    deps = {(a["name"], a["version_spec"], a["dev"]) for a in attrs(facts, "dependency")}
    assert {
        ("fastapi", ">=0.110", False),
        ("pydantic", ">=2", False),
        ("pytest", ">=8", True),
        ("ruff", "", True),
    } <= deps


def test_lockfile_sibling_and_other_ecosystems(tmp_path: Path) -> None:
    ctx = repo(
        tmp_path,
        {
            "web/package.json": '{"dependencies": {"react": "^18"}, "devDependencies": {"vitest": "1"}}',
            "web/pnpm-lock.yaml": "lockfileVersion: 9\n",
            "go.mod": "module x\n\nrequire (\n\tgithub.com/gin-gonic/gin v1.9.1\n)\n",
            "Api/Api.csproj": '<PackageReference Include="Serilog" Version="3.1.1" />',
            "broken/pyproject.toml": "[project\n",
        },
    )
    manifests = {a["path"]: a for a in attrs(extract_manifests(ctx), "manifest")}
    assert manifests["web/package.json"]["has_lockfile"] is True
    assert (
        manifests["go.mod"]["has_lockfile"] is False
        and manifests["go.mod"]["dependency_count"] == 1
    )
    assert manifests["Api/Api.csproj"]["dependency_count"] == 1
    assert manifests["broken/pyproject.toml"]["dependency_count"] == 0  # unparseable, still listed


def test_workspace_root_lockfile_covers_members(tmp_path: Path) -> None:
    ctx = repo(
        tmp_path,
        {
            "pyproject.toml": '[tool.uv.workspace]\nmembers = ["backend"]\n',
            "uv.lock": "version = 1\n",
            "backend/pyproject.toml": '[project]\nname = "app"\ndependencies = ["fastapi>=0.110"]\n',
            "package.json": '{"workspaces": ["frontend"]}',
            "bun.lock": "{}\n",
            "frontend/package.json": '{"dependencies": {"react": "^18"}}',
            "tools/go.mod": "module x\n",  # no go.sum anywhere
            "lib/Cargo.toml": '[dependencies]\nserde = "1"\n',
            "lib/uv.lock": "version = 1\n",  # another ecosystem's lockfile doesn't count
        },
    )
    manifests = {a["path"]: a["has_lockfile"] for a in attrs(extract_manifests(ctx), "manifest")}
    assert manifests["backend/pyproject.toml"] is True and manifests["pyproject.toml"] is True
    assert manifests["frontend/package.json"] is True and manifests["package.json"] is True
    assert manifests["tools/go.mod"] is False
    assert manifests["lib/Cargo.toml"] is False


# --- ci -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("run", "uses", "kind"),
    [
        ("uv run pytest -q", None, "test"),
        ("npm run test", None, "test"),
        ("ruff check .", None, "lint"),
        ("docker build -t x . && docker push x", None, "deploy"),
        ("docker build -t x .", None, "build"),
        (None, "azure/webapps-deploy@v3", "deploy"),
        (None, "docker/build-push-action@v6", "deploy"),
        ("echo hi", None, "other"),
        ("docker compose build", None, "build"),
        ("docker-compose -f ci.yml build api", None, "build"),
        ("docker buildx bake --load", None, "build"),
        ("uv run prek run --all-files", None, "lint"),
        (None, "pre-commit/action@v3.0.1", "lint"),
        (None, "golangci/golangci-lint-action@v6", "lint"),
        ("npx biome ci .", None, "lint"),
        ('uv run bash scripts/tests-start.sh "Coverage"', None, "test"),
        ("./scripts/test.sh", None, "test"),
        ("pwsh ./build/run-tests.ps1", None, "test"),
        ("coverage run -m unittest discover", None, "test"),
        ("bash scripts/prestart.sh", None, "other"),
        ("docker compose up -d --wait db", None, "other"),
        ("python manage.py collectstatic", None, "other"),
    ],
)
def test_classify_step(run: str | None, uses: str | None, kind: str) -> None:
    assert classify_step(run, uses) == kind


def test_is_pinned() -> None:
    assert is_pinned("actions/checkout@" + "a" * 40)
    assert is_pinned("./.github/actions/setup") and is_pinned("docker://alpine:3")
    assert not is_pinned("actions/checkout@v4")


GITHUB_WORKFLOW = """name: ci
on:
  push:
  pull_request:
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Tests
        run: |
          uv sync
          uv run pytest --token=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789
  deploy:
    environment: prod
    permissions:
      id-token: write
    steps:
      - run: azd deploy
"""


def test_github_actions(tmp_path: Path) -> None:
    facts = extract_ci(repo(tmp_path, {".github/workflows/ci.yml": GITHUB_WORKFLOW}))
    workflow = attrs(facts, "ci_workflow")[0]
    assert workflow["triggers"] == ["push", "pull_request"]
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["job_permissions"] == {"test": None, "deploy": {"id-token": "write"}}
    assert workflow["environments"] == {"test": None, "deploy": "prod"}
    steps = [f for f in facts if f.kind == "ci_step"]
    assert [s.attributes["run_kind"] for s in steps] == ["other", "test", "deploy"]
    assert [line(s) for s in steps] == [11, 12, 21]
    assert "ghp_ABCDEF" not in str(steps[1].attributes["run"])  # redacted


def test_other_ci_systems(tmp_path: Path) -> None:
    ctx = repo(
        tmp_path,
        {
            ".gitlab-ci.yml": "stages: [test]\nunit:\n  stage: test\n  script:\n    - pytest -q\n",
            "azure-pipelines.yml": "jobs:\n  - job: build\n    steps:\n      - script: dotnet build\n      - task: DotNetCoreCLI@2\n",
            ".circleci/config.yml": "jobs:\n  lint:\n    steps:\n      - checkout\n      - run: npm run lint\n",
            "Jenkinsfile": "pipeline { stages { stage('t') { steps { sh 'go test ./...' } } } }\n",
        },
    )
    steps = {(a["system"], a["job"], a["run_kind"]) for a in attrs(extract_ci(ctx), "ci_step")}
    assert ("gitlab_ci", "unit", "test") in steps
    assert ("azure_pipelines", "build", "build") in steps
    assert ("circleci", "lint", "lint") in steps
    assert ("jenkins", "pipeline", "test") in steps


def test_tiny_service_ci(tiny_service: MaterializedRepo, tmp_path: Path) -> None:
    facts = extract_ci(tiny(tiny_service, tmp_path))
    kinds = {a["run_kind"] for a in attrs(facts, "ci_step")}
    assert {"lint", "build", "deploy"} <= kinds and "test" not in kinds  # D17, D20
    assert attrs(facts, "ci_workflow")[0]["permissions"] is None  # D21
    login = next(a for a in attrs(facts, "ci_step") if a["uses"] == "docker/login-action@v3")
    assert login["pinned"] is False and login["with_keys"] == [
        "registry",
        "username",
        "password",
    ]  # D22


# --- docker / deploy ----------------------------------------------------------------------------


def test_parse_dockerfile_multistage() -> None:
    lines = [
        "# syntax=docker/dockerfile:1",
        "FROM --platform=linux/amd64 python:3.12-slim AS build",
        "RUN pip install \\",
        "    --no-cache-dir uv",
        "USER root",
        "FROM build AS test",
        "FROM gcr.io/distroless/python3@sha256:" + "0" * 64,
        "USER 65532",
        "HEALTHCHECK NONE",
    ]
    parsed = parse_dockerfile(lines)
    assert parsed is not None
    attributes, final_line = parsed
    assert attributes["stages"] == 3 and attributes["stage_aliases"] == ["build", "test"]
    assert attributes["external_bases"] == [
        "python:3.12-slim",
        "gcr.io/distroless/python3@sha256:" + "0" * 64,
    ]
    assert attributes["user"] == "65532" and attributes["has_healthcheck"] is False
    assert final_line == 7


def test_tiny_service_dockerfile(tiny_service: MaterializedRepo, tmp_path: Path) -> None:
    facts = extract_dockerfiles(tiny(tiny_service, tmp_path))
    attributes = attrs(facts, "dockerfile")[0]
    assert attributes["user"] is None and attributes["stages"] == 1  # D25, D27
    assert (
        attributes["final_base"] == "python:latest" and attributes["has_healthcheck"] is False
    )  # D26, D29


def test_compose_and_kubernetes(tmp_path: Path) -> None:
    compose = (
        "services:\n  api:\n    image: x\n    deploy:\n      resources:\n        limits:\n          cpus: '1'\n"
        "    healthcheck:\n      test: [CMD, curl, -f, localhost]\n  worker:\n    image: y\n    mem_limit: 512m\n"
        "    healthcheck:\n      disable: true\n"
    )
    k8s = (
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: api\nspec:\n  template:\n    spec:\n"
        "      containers:\n        - name: api\n          resources:\n            limits: {cpu: '1'}\n"
        "          livenessProbe: {httpGet: {path: /health, port: 8000}}\n"
        "---\napiVersion: batch/v1\nkind: CronJob\nmetadata:\n  name: nightly\nspec:\n  jobTemplate:\n"
        "    spec:\n      template:\n        spec:\n          containers:\n            - name: job\n"
    )
    ctx = repo(
        tmp_path,
        {
            "compose.yaml": compose,
            "k8s/app.yaml": k8s,
            "chart/templates/d.yaml": "{{ .Values }}: x: :\n",
        },
    )
    found = attrs(extract_deploy_configs(ctx), "deploy_config")
    configs = {(a["kind"], a["name"]): a for a in found}
    assert len(configs) == len(found) == 4  # the Helm template is skipped
    api, worker = configs[("compose_service", "api")], configs[("compose_service", "worker")]
    assert api["has_limits"] is True and api["has_healthcheck"] is True
    assert worker["has_limits"] is True and worker["has_healthcheck"] is False
    k8s_api = configs[("k8s_workload", "api")]
    assert k8s_api["has_limits"] is True and k8s_api["has_liveness"] is True
    assert k8s_api["has_readiness"] is False
    assert configs[("k8s_workload", "nightly")]["has_limits"] is False


# --- routes -------------------------------------------------------------------------------------


def test_tiny_service_routes(tiny_service: MaterializedRepo, tmp_path: Path) -> None:
    routes = {
        (a["method"], a["path"])
        for a in attrs(extract_routes(tiny(tiny_service, tmp_path)), "route")
    }
    assert ("GET", "/health") in routes  # C01
    assert {("POST", "/users"), ("GET", "/users/search"), ("DELETE", "/users/{user_id}")} <= routes


def test_routes_across_stacks(tmp_path: Path) -> None:
    ctx = repo(
        tmp_path,
        {
            "flask_app.py": "from flask import Flask\napp = Flask(__name__)\n@app.route('/items', methods=['GET', 'POST'])\ndef items(): ...\n",
            "shop/urls.py": "from django.urls import path\nfrom . import views\nurlpatterns = [path('orders/', views.orders)]\n",
            "server.js": "app.get('/api/users', handler)\nrouter.post(\"/api/users\", create)\n",
            "users.controller.ts": "@Controller('users')\nclass C {\n  @Get(':id')\n  find() {}\n}\n",
            "Program.cs": 'app.MapGet("/todos", () => db.Todos);\n[HttpPost("create")]\n',
            "Api.java": '@GetMapping("/books")\npublic List<Book> all() {}\n',
            "main.go": 'r.GET("/ping", ping)\nhttp.HandleFunc("/health", h)\n',
        },
    )
    found = {(a["framework"], a["method"], a["path"]) for a in attrs(extract_routes(ctx), "route")}
    assert {
        ("flask", "GET", "/items"),
        ("flask", "POST", "/items"),
        ("django", "ANY", "/orders/"),
    } <= found
    assert {
        ("express", "GET", "/api/users"),
        ("express", "POST", "/api/users"),
        ("nestjs", "GET", ":id"),
    } <= found
    assert {
        ("aspnetcore", "GET", "/todos"),
        ("aspnetcore", "POST", "create"),
        ("spring", "GET", "/books"),
    } <= found
    assert {("go", "GET", "/ping"), ("go", "ANY", "/health")} <= found


# --- tests / logging / metrics / docs ----------------------------------------------------------


def test_test_files(tmp_path: Path) -> None:
    ctx = repo(
        tmp_path,
        {
            "tests/test_a.py": "import pytest\ndef test_x():\n    assert 1\n    with pytest.raises(ValueError): f()\n",
            "tests/test_b.py": "import unittest\nclass T(unittest.TestCase):\n    def test_y(self):\n        self.assertEqual(1, 1)\n",
            "web/app.test.ts": "import { it, expect } from 'vitest'\nit('a', () => { expect(1).toBe(1) })\ntest('b', () => {})\n",
            "pkg/x_test.go": 'func TestX(t *testing.T) { t.Fatal("x") }\n',
            "app/helpers.py": "def test_like_but_not_a_test(): pass\n",
        },
    )
    found = {
        a["path"]: (a["framework"], a["test_count"], a["assert_count"])
        for a in attrs(extract_test_files(ctx), "test_file")
    }
    assert found == {
        "tests/test_a.py": ("pytest", 1, 2),
        "tests/test_b.py": ("unittest", 1, 1),
        "web/app.test.ts": ("vitest", 2, 1),
        "pkg/x_test.go": ("go-testing", 1, 1),
    }


def test_log_and_print_calls(tmp_path: Path) -> None:
    ctx = repo(
        tmp_path,
        {
            "app/svc.py": "import logging\nlogger = logging.getLogger(__name__)\nlogger.info('x')\nprint('debug')\nitems.count('x')\n",
            "tests/test_svc.py": "def test_a():\n    print('in test')\n",
            "web/a.js": "console.log('x')\nlogger.warn('y')\n",
        },
    )
    facts = extract_log_calls(ctx)
    prints = {(a["path"], a["in_test"]) for a in attrs(facts, "print_call")}
    assert prints == {("app/svc.py", False), ("tests/test_svc.py", True), ("web/a.js", False)}
    logs = {(a["path"], a["level"]) for a in attrs(facts, "log_call")}
    assert logs == {("app/svc.py", "info"), ("web/a.js", "warn")}


def test_file_metrics(tmp_path: Path) -> None:
    ctx = repo(
        tmp_path,
        {
            "app/a.py": 'def pub():\n    """Doc."""\ndef _priv(): ...\nclass C:\n    def m(self): ...\n',
            "web/b.ts": "/** Adds. */\nexport function add() {}\nexport const sub = () => 1\n",
            "tests/test_a.py": "def test_x(): ...\n",
            "dist/bundle.js": "var a=1;\n",
        },
    )
    metrics = {a["path"]: a for a in attrs(extract_file_metrics(ctx), "file_metrics")}
    assert (
        metrics["app/a.py"]["public_functions"],
        metrics["app/a.py"]["documented_functions"],
    ) == (2, 1)
    assert (
        metrics["web/b.ts"]["public_functions"],
        metrics["web/b.ts"]["documented_functions"],
    ) == (2, 1)
    assert metrics["tests/test_a.py"]["is_test"] is True
    assert metrics["dist/bundle.js"]["is_generated"] is True


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("README.md", "readme"),
        ("docs/adr/0001-use-sqlite.md", "adr"),
        ("docs/decisions/x.md", "adr"),
        ("docs/ARCHITECTURE.md", "architecture"),
        ("api/openapi.yaml", "openapi"),
        ("CONTRIBUTING.md", "contributing"),
        ("CHANGELOG.md", "changelog"),
        ("docs/guide.md", None),
    ],
)
def test_doc_type(path: str, kind: str | None) -> None:
    assert doc_type(path) == kind


def test_a_failing_extractor_is_recorded_not_raised(
    tiny_service: MaterializedRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(ctx: ScanContext) -> list[Fact]:
        raise RuntimeError("parser exploded")

    monkeypatch.setitem(extractors_module.EXTRACTORS, "ast:routes", boom)
    facts, runs = run_extractors(tiny(tiny_service, tmp_path))
    status = {r.tool: r.status for r in runs}
    assert status["ast:routes"] == "error" and status["ast:imports"] == "ok"
    assert not any(f.kind == "route" for f in facts)
