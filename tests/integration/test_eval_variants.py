"""The shipped variants applied to the real, fetched eval repos (`pytest -m scanners`).

Needs `archlens eval fetch` and the scanner binaries; never calls an LLM. Every variant must apply
cleanly (patched Python still parses, edited workflows are still YAML), and for every expected
check with a deterministic rule, the rule on the mutated copy must give the expected verdict —
so a mutation that doesn't actually produce its defect fails here, before any money is spent.
"""

import ast
import shutil
import tempfile
from functools import cache
from pathlib import Path

import pytest
import yaml

from archlens.config import Settings, load_config
from archlens.eval.metrics import detected
from archlens.eval.mutations import MUTATIONS
from archlens.eval.repos import checkout_path, load_repos, marker_path
from archlens.eval.variants import VariantSpec, apply_variant, load_variants
from archlens.evidence import SnippetReader
from archlens.facts.runner import collect_facts
from archlens.ingest.snapshot import build_snapshot
from archlens.models import FactSet, IngestLimits, RepoProfile, Verdict
from archlens.profile import build_profile
from archlens.rubric import RuleContext, load_rubrics, run_rule

pytestmark = pytest.mark.scanners
REPO = Path(__file__).parents[2]
SETTINGS = Settings(_env_file=None, config_dir=REPO / "config")  # pyright: ignore[reportCallIssue]
CACHE = REPO / SETTINGS.data_dir / "eval" / "repos"
REPOS = {r.id: r for r in load_repos(REPO / "eval" / "repos.yaml")}
VARIANTS = load_variants(REPO / "eval" / "variants.yaml")
RUBRICS = load_rubrics(REPO / "rubrics")
SPECS = {c.id: (r, c) for r in RUBRICS.values() for c in r.checks}
SCANNER_CHECKS = {"SEC-01", "SEC-02", "SEC-03", "CI-03", "STR-03", "CTR-07"}


def checkout(repo_id: str) -> Path:
    path = checkout_path(REPOS[repo_id], CACHE)
    if not marker_path(path).is_file():
        pytest.skip("eval repos not fetched; run `archlens eval fetch`")
    return path


def facts_of(root: Path, work: Path, *, scanners: bool) -> tuple[FactSet, RepoProfile, RuleContext]:
    snapshot = build_snapshot(root, limits=IngestLimits())
    found = collect_facts(
        root, snapshot, workdir=work, tools=load_config(SETTINGS).tools,
        tools_dir=SETTINGS.tools_dir, scanners=scanners,
    )  # fmt: skip
    profile = build_profile(snapshot, found.facts, SnippetReader(root, found.redactor))
    return (
        found.facts,
        profile,
        RuleContext.create(root, found.facts, profile, snapshot.files, found.redactor),
    )


@cache
def base_facts(repo_id: str) -> tuple[FactSet, RepoProfile]:
    with tempfile.TemporaryDirectory() as work:
        facts, profile, _ = facts_of(checkout(repo_id), Path(work), scanners=True)
    return facts, profile


def effective(spec: VariantSpec) -> tuple[VariantSpec, list[str]]:
    facts, profile = base_facts(spec.repo)
    entries = (
        sorted(m.id for m in MUTATIONS.values() if m.generic and not m.injection)
        if spec.all_generic
        else spec.mutations
    )
    kept = [
        e for e in entries
        if all(MUTATIONS[m.strip()].precondition(facts, profile) for m in e.split("+"))
    ]  # fmt: skip
    skipped = [m.strip() for e in entries if e not in kept for m in e.split("+")]
    return spec.model_copy(update={"mutations": kept, "all_generic": False}), skipped


@pytest.mark.parametrize("spec", VARIANTS, ids=[v.id for v in VARIANTS])
def test_variant_applies_and_produces_its_defects(spec: VariantSpec, tmp_path: Path) -> None:
    base = checkout(spec.repo)
    copy = tmp_path / "repo"
    shutil.copytree(base, copy, symlinks=True, ignore=shutil.ignore_patterns(".git"))
    variant, skipped = effective(spec)
    assert variant.mutations, f"{spec.id}: every mutation was skipped ({skipped})"
    applied = apply_variant(variant, copy, MUTATIONS, seed=7)

    for item in applied:
        for change in item.result.changed:
            path = copy / change.path
            if not path.exists():  # deleted (M-NOTESTS)
                continue
            text = path.read_text()
            assert 1 <= change.start_line <= change.end_line <= max(len(text.splitlines()), 1), (
                change
            )
            if change.path.endswith(".py"):
                ast.parse(text, filename=change.path)
            if change.path.endswith((".yml", ".yaml")):
                yaml.safe_load(text)

    expected: dict[str, Verdict] = {c: v for a in applied for c, v in a.mutation.expected.items()}
    deterministic: dict[str, Verdict] = {
        c: v for c, v in expected.items() if SPECS[c][1].type == "deterministic"
    }
    if deterministic:
        scanners = bool(set(deterministic) & SCANNER_CHECKS)
        _, _, ctx = facts_of(copy, tmp_path / "work", scanners=scanners)
        for check_id, verdict in deterministic.items():
            rubric, check = SPECS[check_id]
            result = run_rule(check, rubric, ctx)
            assert detected(verdict, result.verdict), (spec.id, check_id, result.claim)
