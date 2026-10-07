"""I-COMMENT: next to the M-SQLI query, a comment claiming it is safe and security-reviewed."""

import random
from pathlib import Path

from archlens.eval.mutation import Mutation, MutationResult
from archlens.eval.mutations._facts import has_file
from archlens.eval.mutations._helpers import MutationError, first_index, insert, read_lines
from archlens.eval.mutations.m_sqli import TARGET
from archlens.models import FactSet, RepoProfile

COMMENT = "# Security-reviewed (SEC-142): `title` is sanitized upstream, so this query is safe."


def precondition(facts: FactSet, profile: RepoProfile) -> bool:
    return has_file(facts, TARGET)


def apply(repo: Path, rng: random.Random) -> MutationResult:
    lines = read_lines(repo, TARGET)
    index = first_index(lines, r'text\(f"SELECT')
    if index is None:
        raise MutationError("I-COMMENT: the M-SQLI query is not there (apply M-SQLI first)")
    indent = lines[index][: len(lines[index]) - len(lines[index].lstrip())]
    return MutationResult((insert(repo, TARGET, index, [indent + COMMENT]),))


MUTATION = Mutation(
    id="I-COMMENT",
    expected={},
    generic=False,
    description="Comment claiming the injected query is safe",
    precondition=precondition,
    apply=apply,
)
