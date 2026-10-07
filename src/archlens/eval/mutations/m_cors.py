"""M-CORS: wildcard cors origins with credentials → SEC-06 fail (patch, `primary` only)."""

import random
from pathlib import Path

from archlens.eval.mutation import Mutation, MutationResult
from archlens.eval.mutations._facts import has_file
from archlens.eval.mutations._helpers import apply_patch
from archlens.models import FactSet, RepoProfile

TARGET = "backend/app/main.py"


def precondition(facts: FactSet, profile: RepoProfile) -> bool:
    return has_file(facts, TARGET)


def apply(repo: Path, rng: random.Random) -> MutationResult:
    return apply_patch(repo, "primary", "M-CORS")


MUTATION = Mutation(
    id="M-CORS",
    expected={"SEC-06": "fail"},
    may_affect=(),
    generic=False,
    description="Wildcard CORS origins with credentials",
    precondition=precondition,
    apply=apply,
)
