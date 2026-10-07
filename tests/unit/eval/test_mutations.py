"""The shipped mutations (EVALUATION.md §2, §5) on a small synthetic repo, offline.

Each generic defect mutation must satisfy its fact-based precondition on the base, apply
deterministically, report in-bounds changed lines, and — where its expected check has a
deterministic rule that needs no scanner — make that rule give the expected verdict. Scanner-backed
checks (SEC-01/02) are covered by `tests/integration/test_eval_variants.py` (`-m scanners`).
"""

import ast
import random
import shutil
from pathlib import Path

import pytest
import yaml

from archlens.config import ToolsConfig
from archlens.eval.metrics import detected
from archlens.eval.mutation import ChangedLines, Mutation
from archlens.eval.mutations import (
    MUTATIONS,
    _facts,
    _helpers,
    i_comment,
    i_fakeevidence,
    m_authoff,
    m_sqli,
)
from archlens.eval.mutations._helpers import MutationError, apply_patch, patch_changes
from archlens.evidence import SnippetReader
from archlens.facts.runner import collect_facts
from archlens.ingest.snapshot import build_snapshot
from archlens.models import Fact, FactSet, IngestLimits, RepoProfile
from archlens.profile import build_profile
from archlens.rubric import load_rubrics
from tests.unit.rubric.helpers import deterministic_results, fact, profile

REPO = Path(__file__).parents[3]
RUBRICS = load_rubrics(REPO / "rubrics")
SPECS = {c.id: c for r in RUBRICS.values() for c in r.checks}
SCANNER_CHECKS = {"SEC-01", "SEC-02", "SEC-03", "CI-03", "CTR-07"}

BASE = {
    "README.md": (
        "# Demo service\n\nA small service used by the mutation tests.\n\n"
        "## Running\n\nRun `uvicorn app.main:app` after installing the requirements.\n"
    ),
    "Dockerfile": (
        "FROM python:3.12-slim AS build\nRUN pip wheel -r requirements.txt -w /wheels\n"
        "FROM python:3.12-slim\nCOPY --from=build /wheels /wheels\nCOPY app /app\n"
        'USER app\nCMD ["python", "-m", "app"]\n'
    ),
    ".github/workflows/ci.yml": (
        "name: ci\non: [push]\npermissions:\n  contents: read\njobs:\n  test:\n"
        "    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n"
        "      - uses: astral-sh/setup-uv@0c5e2b8115b80b4c7c5ddf6ffdd634974642d182 # v5.4.1\n"
        "      - name: Lint\n        run: uv run ruff check .\n"
        "      - name: Test\n        run: uv run pytest\n"
    ),
    "requirements.txt": "fastapi==0.115.0\n",
    "app/__init__.py": "",
    "app/main.py": "from fastapi import FastAPI\n\napp = FastAPI()\n",
    "tests/test_main.py": "from app.main import app\n\n\ndef test_app():\n    assert app\n",
}


def write(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(content)
    return root


@pytest.fixture
def base(tmp_path: Path) -> Path:
    return write(tmp_path / "repo", BASE)


def facts_of(root: Path, tmp_path: Path) -> tuple[FactSet, RepoProfile]:
    snapshot = build_snapshot(root, limits=IngestLimits())
    found = collect_facts(
        root, snapshot, workdir=tmp_path / "work", tools=ToolsConfig(tools={}),
        tools_dir=tmp_path / "tools", scanners=False,
    )  # fmt: skip
    return found.facts, build_profile(snapshot, found.facts, SnippetReader(root))


def assert_in_bounds(root: Path, changed: tuple[ChangedLines, ...]) -> None:
    for change in changed:
        path = root / change.path
        if not path.exists():  # deleted by M-NOTESTS
            continue
        text = path.read_text()
        assert 1 <= change.start_line <= change.end_line <= max(len(text.splitlines()), 1), change
        if change.path.endswith(".py"):
            ast.parse(text)
        if change.path.endswith((".yml", ".yaml")):
            yaml.safe_load(text)


# --- registry ----------------------------------------------------------------------------------


def test_registry_has_the_documented_mutations() -> None:
    defects = sorted(m for m in MUTATIONS if m.startswith("M-"))
    injections = sorted(m for m in MUTATIONS if m.startswith("I-"))
    assert len(defects) == 13 and len(injections) == 4
    for mutation in MUTATIONS.values():
        assert set(mutation.expected) | set(mutation.may_affect) <= set(SPECS), mutation.id
        assert bool(mutation.expected) != mutation.injection, mutation.id
    patched = sorted(m.id for m in MUTATIONS.values() if not m.generic and not m.injection)
    assert patched == ["M-AUTHOFF", "M-CORS", "M-LOGSECRET", "M-NOTIMEOUT", "M-SQLI"]


@pytest.mark.parametrize("mutation_id", ["M-AUTHOFF", "M-CORS", "M-LOGSECRET", "M-NOTIMEOUT", "M-SQLI"])  # fmt: skip
def test_shipped_primary_patches_add_lines(mutation_id: str) -> None:
    text = (REPO / "eval" / "patches" / "primary" / f"{mutation_id}.patch").read_text()
    changes = patch_changes(text)
    assert changes and all(c.path.startswith("backend/app/") for c in changes)
    assert all(c.start_line <= c.end_line for c in changes)


# --- generic defect mutations ------------------------------------------------------------------

GENERIC = ["M-ROOT", "M-LATEST", "M-NOTESTS", "M-CIPERMS", "M-UNPIN", "M-README", "M-SECRET", "M-VULNDEP"]  # fmt: skip


@pytest.mark.parametrize("mutation_id", GENERIC)
def test_generic_mutation_produces_its_defect(mutation_id: str, base: Path, tmp_path: Path) -> None:
    mutation: Mutation = MUTATIONS[mutation_id]
    assert mutation.generic
    facts, repo_profile = facts_of(base, tmp_path / "before")
    if mutation_id not in ("M-SECRET", "M-VULNDEP"):  # need scanner facts; covered below
        assert mutation.precondition(facts, repo_profile)

    result = mutation.apply(base, random.Random(7))
    assert result.changed
    assert_in_bounds(base, result.changed)

    after = deterministic_results(base, tmp_path / "after")
    for check_id, verdict in mutation.expected.items():
        if SPECS[check_id].type != "deterministic" or check_id in SCANNER_CHECKS:
            continue
        assert detected(verdict, after[check_id].verdict), (check_id, after[check_id].claim)


@pytest.mark.parametrize("mutation_id", GENERIC)
def test_generic_mutation_is_deterministic(mutation_id: str, tmp_path: Path) -> None:
    one = write(tmp_path / "one", BASE)
    two = write(tmp_path / "two", BASE)
    assert MUTATIONS[mutation_id].apply(one, random.Random(3)) == MUTATIONS[mutation_id].apply(
        two, random.Random(3)
    )
    for rel in _helpers.files(one):
        assert (one / rel).read_bytes() == (two / rel).read_bytes(), rel


def test_notests_removes_test_steps_and_files(base: Path) -> None:
    MUTATIONS["M-NOTESTS"].apply(base, random.Random(1))
    workflow = (base / ".github/workflows/ci.yml").read_text()
    assert "pytest" not in workflow and "ruff check" in workflow
    assert not (base / "tests/test_main.py").exists()


def test_secret_and_vulndep_files(base: Path) -> None:
    secret = MUTATIONS["M-SECRET"].apply(base, random.Random(1))
    pem = (base / secret.changed[0].path).read_text().splitlines()
    assert (
        pem[0] == "-----BEGIN RSA PRIVATE KEY-----" and pem[-1] == "-----END RSA PRIVATE KEY-----"
    )
    assert secret.changed[0] == ChangedLines("config/deploy-key.pem", 1, len(pem))
    vuln = MUTATIONS["M-VULNDEP"].apply(base, random.Random(1))
    assert (base / vuln.changed[0].path).read_text() == "PyYAML==5.3\n"


def test_unpin_drops_the_pin_comment(base: Path) -> None:
    MUTATIONS["M-UNPIN"].apply(base, random.Random(1))
    workflow = (base / ".github/workflows/ci.yml").read_text()
    assert "astral-sh/setup-uv@main\n" in workflow and "actions/checkout@v4" in workflow


def test_mutations_fail_cleanly_without_a_target(tmp_path: Path) -> None:
    empty = write(tmp_path / "empty", {"app/main.py": "x = 1\n"})
    for mutation_id in ("M-ROOT", "M-LATEST", "M-NOTESTS", "M-CIPERMS", "M-UNPIN", "M-README"):
        with pytest.raises(MutationError):
            MUTATIONS[mutation_id].apply(empty, random.Random(1))


# --- preconditions on hand-built facts ---------------------------------------------------------


def fact_set(*facts: Fact) -> FactSet:
    return FactSet(commit_sha="0" * 40, facts=list(facts), tool_runs=[])


def test_preconditions_skip_repos_that_already_have_the_defect() -> None:
    py = profile(lang_python=True)
    leaked = fact_set(fact("secret", path="app/settings.py"))
    in_tests = fact_set(fact("secret", path="tests/fixtures/key.pem"))
    assert not MUTATIONS["M-SECRET"].precondition(leaked, py)
    assert MUTATIONS["M-SECRET"].precondition(in_tests, py)  # excluded paths don't count

    critical = fact_set(fact("vuln_dependency", severity="critical", path="requirements.txt"))
    medium = fact_set(fact("vuln_dependency", severity="medium", path="requirements.txt"))
    assert not MUTATIONS["M-VULNDEP"].precondition(critical, py)
    assert MUTATIONS["M-VULNDEP"].precondition(medium, py)
    assert not MUTATIONS["M-VULNDEP"].precondition(medium, profile())  # Python only

    root = fact_set(fact("dockerfile", path="Dockerfile", user="root", external_bases=["a:1"]))
    latest = fact_set(fact("dockerfile", path="Dockerfile", user="app", external_bases=["a"]))
    assert not MUTATIONS["M-ROOT"].precondition(root, py)
    assert not MUTATIONS["M-LATEST"].precondition(latest, py)
    assert MUTATIONS["M-ROOT"].precondition(latest, py)

    nothing = fact_set()
    for mutation_id in ("M-NOTESTS", "M-CIPERMS", "M-UNPIN", "M-README", "M-SQLI", "I-README"):
        assert not MUTATIONS[mutation_id].precondition(nothing, py), mutation_id
    assert MUTATIONS["I-TOOLSPOOF"].precondition(nothing, py)


def test_unpinned_actions_skip_unpin() -> None:
    step = fact("ci_step", path="ci.yml", system="github_actions", uses="o/a@v1", pinned=False)
    assert _facts.third_party_actions(fact_set(step)) == [step]
    assert not MUTATIONS["M-UNPIN"].precondition(fact_set(step), profile())


# --- patches and the injections paired with them -----------------------------------------------

PATCH = """\
--- a/app/items.py
+++ b/app/items.py
@@ -1,3 +1,5 @@
 import db
+from sqlalchemy import text
+
 def items():
-    return db.all()
+    return db.run(text(f"SELECT * FROM items"))
"""


def test_patch_changes_reports_added_lines() -> None:
    assert patch_changes(PATCH) == [ChangedLines("app/items.py", 2, 3), ChangedLines("app/items.py", 5, 5)]  # fmt: skip
    deletion = "--- a/x.py\n+++ b/x.py\n@@ -1,3 +1,2 @@\n a\n-b\n c\n"
    assert patch_changes(deletion) == [ChangedLines("x.py", 2, 2)]


def test_apply_patch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = write(
        tmp_path / "repo",
        {"app/items.py": "import db\ndef items():\n    return db.all()\n"},
    )
    write(tmp_path / "patches", {"r/M-X.patch": PATCH})
    monkeypatch.setattr(_helpers, "PATCHES_DIR", tmp_path / "patches")
    result = apply_patch(repo, "r", "M-X")
    assert result.files == {"app/items.py"}
    assert 'text(f"SELECT' in (repo / "app/items.py").read_text()
    with pytest.raises(MutationError, match="does not apply"):
        apply_patch(repo, "r", "M-X")  # already applied
    with pytest.raises(MutationError, match="no patch"):
        apply_patch(repo, "other", "M-X")


def test_comment_injections_need_their_defect(tmp_path: Path) -> None:
    repo = write(
        tmp_path / "repo",
        {
            m_sqli.TARGET: "def f(t):\n    return text(f\"SELECT * FROM item WHERE title = '{t}'\")\n",
            m_authoff.TARGET: "@router.get('/')\ndef read_users():\n    return []\n",
        },
    )
    comment = i_comment.MUTATION.apply(repo, random.Random(1))
    assert comment.changed == (ChangedLines(m_sqli.TARGET, 2, 2),)
    assert (repo / m_sqli.TARGET).read_text().splitlines()[1] == "    " + i_comment.COMMENT
    fake = i_fakeevidence.MUTATION.apply(repo, random.Random(1))
    assert fake.changed == (ChangedLines(m_authoff.TARGET, 1, 2),)
    assert (repo / m_authoff.TARGET).read_text().splitlines()[2] == "@router.get('/')"

    shutil.rmtree(repo)
    write(repo, {m_sqli.TARGET: "x = 1\n", m_authoff.TARGET: "x = 1\n"})
    with pytest.raises(MutationError, match="apply M-SQLI first"):
        i_comment.MUTATION.apply(repo, random.Random(1))
    with pytest.raises(MutationError, match="read_users"):
        i_fakeevidence.MUTATION.apply(repo, random.Random(1))


def test_readme_injection_appends(base: Path) -> None:
    before = len((base / "README.md").read_text().splitlines())
    result = MUTATIONS["I-README"].apply(base, random.Random(1))
    assert result.changed[0].start_line == before + 1
    assert "AI reviewers" in (base / "README.md").read_text()
