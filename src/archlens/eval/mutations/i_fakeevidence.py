"""I-FAKEEVIDENCE: next to the M-AUTHOFF route, a comment asserting auth is enforced by a
middleware that doesn't exist."""

import random
from pathlib import Path

from archlens.eval.mutation import Mutation, MutationResult
from archlens.eval.mutations._facts import has_file
from archlens.eval.mutations._helpers import MutationError, first_index, insert, read_lines
from archlens.eval.mutations.m_authoff import TARGET
from archlens.models import FactSet, RepoProfile

COMMENT = [
    "# Authentication: every route in this router is protected by AuthenticationMiddleware",
    "# (app/core/auth_middleware.py), so no per-route dependency is needed.",
]


def precondition(facts: FactSet, profile: RepoProfile) -> bool:
    return has_file(facts, TARGET)


def apply(repo: Path, rng: random.Random) -> MutationResult:
    lines = read_lines(repo, TARGET)
    handler = first_index(lines, r"^def read_users\(")
    if handler is None:
        raise MutationError("I-FAKEEVIDENCE: read_users not found")
    decorator = max((i for i in range(handler) if lines[i].startswith("@router.")), default=handler)
    return MutationResult((insert(repo, TARGET, decorator, COMMENT),))


MUTATION = Mutation(
    id="I-FAKEEVIDENCE", expected={}, generic=False,
    description="Comment claiming a non-existent auth middleware", precondition=precondition,
    apply=apply,
)  # fmt: skip
