"""M-SQLI: add a query built with an f-string from a request parameter → SEC-05 fail.

Applied from `eval/patches/primary/M-SQLI.patch`; `primary` only.
"""

import random
from pathlib import Path

from archlens.eval.mutation import Mutation, MutationResult
from archlens.eval.mutations._facts import has_file
from archlens.eval.mutations._helpers import apply_patch
from archlens.models import FactSet, RepoProfile

TARGET = "backend/app/api/routes/items.py"


def precondition(facts: FactSet, profile: RepoProfile) -> bool:
    return has_file(facts, TARGET)


def apply(repo: Path, rng: random.Random) -> MutationResult:
    return apply_patch(repo, "primary", "M-SQLI")


MUTATION = Mutation(
    id="M-SQLI",
    expected={"SEC-05": "fail"},
    may_affect=("SEC-03", "SEC-04"),
    generic=False,
    description="Add a query built with an f-string from a request parameter",
    precondition=precondition,
    apply=apply,
)
