"""M-AUTHOFF: remove the superuser dependency from the user list route → AUTH-01 partial (the
rubric's verdict for one unprotected route among protected ones).

Applied from `eval/patches/primary/M-AUTHOFF.patch`; `primary` only.
"""

import random
from pathlib import Path

from archlens.eval.mutation import Mutation, MutationResult
from archlens.eval.mutations._facts import has_file
from archlens.eval.mutations._helpers import apply_patch
from archlens.models import FactSet, RepoProfile

TARGET = "backend/app/api/routes/users.py"


def precondition(facts: FactSet, profile: RepoProfile) -> bool:
    return has_file(facts, TARGET)


def apply(repo: Path, rng: random.Random) -> MutationResult:
    return apply_patch(repo, "primary", "M-AUTHOFF")


MUTATION = Mutation(
    id="M-AUTHOFF",
    expected={"AUTH-01": "partial"},
    may_affect=("AUTH-02", "AUTH-03"),
    generic=False,
    description="Remove the superuser dependency from the user list route",
    precondition=precondition,
    apply=apply,
)
