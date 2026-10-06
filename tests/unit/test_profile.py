"""Profile detectors (table-driven) and the checked-in tiny_service profile."""

from pathlib import Path

import pytest

from archlens.config import ToolsConfig
from archlens.evidence import SnippetReader
from archlens.facts.runner import collect_facts
from archlens.ingest.snapshot import build_snapshot
from archlens.models import IngestLimits, RepoProfile
from archlens.profile import FLAGS, build_profile
from archlens.profile.detectors import matches
from tests.fixture_repos import FIXTURE_REPOS, MaterializedRepo


def profile_of(root: Path, tmp_path: Path) -> RepoProfile:
    snapshot = build_snapshot(root, limits=IngestLimits())
    facts = collect_facts(
        root,
        snapshot,
        workdir=tmp_path / "w",
        tools=ToolsConfig(tools={}),
        tools_dir=tmp_path / "t",
        scanners=False,
    ).facts
    return build_profile(snapshot, facts, SnippetReader(root))


def write(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(content)
    return root


def test_tiny_service_matches_checked_in_profile(
    tiny_service: MaterializedRepo, tmp_path: Path
) -> None:
    expected = RepoProfile.model_validate_json(
        (FIXTURE_REPOS / "tiny_service.profile.json").read_text()
    )
    assert profile_of(tiny_service.root, tmp_path) == expected


@pytest.mark.parametrize(
    ("name", "prefixes", "expected"),
    [
        ("fastapi", ("fastapi",), True),
        ("fastapi.routing", ("fastapi",), True),
        ("@nestjs/core", ("@nestjs/core",), True),
        ("Microsoft.AspNetCore.Mvc", ("microsoft.aspnetcore",), True),
        ("spring-boot-starter-web", ("spring-boot-starter-web",), True),
        ("fastapi_users", ("fastapi",), False),
        ("expressive", ("express",), False),
    ],
)
def test_matches(name: str, prefixes: tuple[str, ...], expected: bool) -> None:
    assert matches(name, prefixes) is expected


CASES: list[tuple[str, dict[str, str], set[str]]] = [
    ("empty repo", {"README.md": "# x\n"}, set()),
    (
        "flask + celery + requests",
        {
            "app.py": "from flask import Flask\nimport requests\nfrom celery import Celery\n",
            "requirements.txt": "flask==3.0.0\n",
        },
        {"has_http_api", "has_message_consumer", "has_outbound_http", "lang_python"},
    ),
    (
        "express + prisma + global fetch",
        {
            "src/index.ts": "import express from 'express'\nimport { PrismaClient } from '@prisma/client'\nawait fetch('https://x')\n",
            "package.json": '{"dependencies": {"express": "4"}}',
        },
        {"has_http_api", "has_database", "has_outbound_http", "lang_js_ts"},
    ),
    (
        "infra and k8s only",
        {
            "infra/main.bicep": "param x string\n",
            "charts/api/Chart.yaml": "name: api\n",
            "Dockerfile": "FROM x:1\n",
        },
        {"has_iac", "has_k8s", "has_dockerfile"},
    ),
    (
        "ci with deploy and tests",
        {
            ".github/workflows/d.yml": "on: push\njobs:\n  d:\n    steps:\n      - run: pytest\n      - run: azd deploy\n",
            "tests/test_a.py": "def test_a():\n    assert 1\n",
        },
        {"has_ci", "has_deploy_step", "has_tests", "lang_python"},
    ),
    (
        "mostly go, a little python",
        {"main.go": "package main\n" + "func f() {}\n" * 40, "tools/x.py": "print(1)\n"},
        {"lang_go"},
    ),
]


@pytest.mark.parametrize(("label", "files", "true_flags"), CASES, ids=[c[0] for c in CASES])
def test_flags(label: str, files: dict[str, str], true_flags: set[str], tmp_path: Path) -> None:
    profile = profile_of(write(tmp_path / "repo", files), tmp_path)
    assert set(profile.flags) == set(FLAGS)  # every flag present, never missing
    assert {flag for flag, on in profile.flags.items() if on} == true_flags


def test_languages_ordered_by_loc(tmp_path: Path) -> None:
    root = write(
        tmp_path / "repo",
        {"a.py": "x = 1\n" * 5, "b.ts": "let x = 1\n" * 9, "vendor/c.go": "x\n" * 99},
    )
    assert list(profile_of(root, tmp_path).languages.items()) == [("typescript", 9), ("python", 5)]
