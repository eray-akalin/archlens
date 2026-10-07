"""M-NOTESTS: delete test files and CI test steps → TEST-01/02/03 fail, CI-02 partial."""

import random
from pathlib import Path

import yaml

from archlens.eval.mutation import ChangedLines, Mutation, MutationResult
from archlens.eval.mutations._facts import has_tests_in_ci
from archlens.eval.mutations._helpers import (
    MutationError,
    child,
    read_lines,
    test_files,
    workflows,
    write_lines,
    yaml_root,
)
from archlens.facts.extractors.ci import RUN_KINDS
from archlens.models import FactSet, RepoProfile

TEST_PATTERN = dict(RUN_KINDS)["test"]


def precondition(facts: FactSet, profile: RepoProfile) -> bool:
    return has_tests_in_ci(facts)


def _scalar(node: yaml.Node | None, key: str) -> str:
    found = child(node, key)
    return str(found[1].value) if found and isinstance(found[1], yaml.ScalarNode) else ""


def test_step_spans(repo: Path, rel: str) -> list[tuple[int, int]]:
    """0-based [start, end) line spans of steps whose command or action runs tests."""
    jobs = child(yaml_root(repo, rel), "jobs")
    spans: list[tuple[int, int]] = []
    if jobs is None or not isinstance(jobs[1], yaml.MappingNode):
        return spans
    for _, job in jobs[1].value:
        steps = child(job, "steps")
        if steps is None or not isinstance(steps[1], yaml.SequenceNode):
            continue
        for step in steps[1].value:
            text = f"{_scalar(step, 'run')}\n{_scalar(step, 'uses')}"
            if TEST_PATTERN.search(text):
                end = step.end_mark.line if step.end_mark.column == 0 else step.end_mark.line + 1
                spans.append((step.start_mark.line, end))
    return spans


def apply(repo: Path, rng: random.Random) -> MutationResult:
    changed: list[ChangedLines] = []
    for rel in workflows(repo):
        spans = test_step_spans(repo, rel)
        if not spans:
            continue
        lines = read_lines(repo, rel)
        cuts: list[int] = []  # 1-based line where each cut happened, in the edited file
        removed = 0
        for start, end in sorted(spans):
            cuts.append(start - removed + 1)
            removed += end - start
        for start, end in sorted(spans, reverse=True):
            del lines[start:end]
        write_lines(repo, rel, lines)
        last = max(len(lines), 1)
        changed.extend(ChangedLines(rel, min(c, last), min(c, last)) for c in cuts)
        if yaml_root(repo, rel) is None:
            raise MutationError(f"M-NOTESTS: {rel} is no longer valid YAML")
    tests = test_files(repo)
    if not tests or not changed:
        raise MutationError("M-NOTESTS: no test files or CI test steps")
    for rel in tests:
        (repo / rel).unlink()
        changed.append(ChangedLines(rel, 1, 1))
    return MutationResult(tuple(changed))


MUTATION = Mutation(
    id="M-NOTESTS",
    expected={"TEST-01": "fail", "TEST-02": "fail", "TEST-03": "fail", "CI-02": "partial"},
    may_affect=("TEST-04", "TEST-05"), absence=True,
    description="Delete test directories and test steps", precondition=precondition, apply=apply,
)  # fmt: skip
