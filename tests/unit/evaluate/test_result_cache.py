"""Metric result cache key: what invalidates a metric's cached LLM results, and what doesn't."""

from collections.abc import Sequence

from archlens.config import ModelsConfig
from archlens.evaluate.result_cache import metric_inputs_key
from archlens.models import CheckSpec, Fact, FactSet, FileEntry, Rubric
from tests.unit.report.helpers import MODELS, spec

CHECKS = [spec("CI-06", "Tests run in CI", "high").model_copy(update={"fact_kinds": ["ci_job"]})]
RUBRIC = Rubric(
    metric="cicd", title="CI/CD", version="1.0.0", weight=1.0, description="d",
    scope_globs=[".github/**"], checks=CHECKS,
)  # fmt: skip


def entry(path: str, sha: str = "a" * 64) -> FileEntry:
    return FileEntry(path=path, size=10, sha256=sha, language=None, loc=1, is_binary=False,
                     is_generated=False, is_vendored=False)  # fmt: skip


def fact(kind: str, ident: str) -> Fact:
    return Fact(id=f"{kind}:{ident}", kind=kind, source="ast:ci", attributes={}, evidence=[])


FILES = [entry(".github/workflows/ci.yml"), entry("app/main.py")]
FACTS = FactSet(commit_sha="c", facts=[fact("ci_job", "1"), fact("route", "2")], tool_runs=[])


def key(
    *,
    rubric: Rubric = RUBRIC,
    checks: Sequence[CheckSpec] = CHECKS,
    prompt_version: str = "p@1",
    models: ModelsConfig = MODELS,
    files: Sequence[FileEntry] = FILES,
    facts: FactSet = FACTS,
) -> str:
    return metric_inputs_key(
        rubric, checks, prompt_version=prompt_version, models=models, files=files, facts=facts
    )


def test_stable_and_order_independent() -> None:
    assert key() == key(files=list(reversed(FILES)))
    assert len(key()) == 64


def test_files_outside_the_scope_and_unrelated_facts_do_not_matter() -> None:
    assert key(files=[FILES[0], entry("app/main.py", "b" * 64), entry("README.md")]) == key()
    other = FactSet(commit_sha="d", facts=[*FACTS.facts, fact("route", "3")], tool_runs=[])
    assert key(facts=other) == key()


def test_inputs_that_change_the_key() -> None:
    changed_ci = [entry(".github/workflows/ci.yml", "b" * 64), FILES[1]]
    new_ci_file = [*FILES, entry(".github/dependabot.yml")]
    new_fact = FactSet(commit_sha="c", facts=[*FACTS.facts, fact("ci_job", "9")], tool_runs=[])
    roles = MODELS.roles.model_copy(update={"evaluator": MODELS.roles.evaluator.model_copy(
        update={"deployment": "other"})})  # fmt: skip
    variants = [
        key(files=changed_ci),
        key(files=new_ci_file),
        key(facts=new_fact),
        key(prompt_version="p@2"),
        key(rubric=RUBRIC.model_copy(update={"version": "1.0.1"})),
        key(checks=[]),
        key(models=MODELS.model_copy(update={"roles": roles})),
    ]
    assert key() not in variants and len(set(variants)) == len(variants)
