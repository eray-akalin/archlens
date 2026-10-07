"""M-LOGSECRET: log the issued access token in the login handler → LOG-04 fail.

Applied from `eval/patches/primary/M-LOGSECRET.patch`; `primary` only.
"""

import random
from pathlib import Path

from archlens.eval.mutation import Mutation, MutationResult
from archlens.eval.mutations._facts import has_file
from archlens.eval.mutations._helpers import apply_patch
from archlens.models import FactSet, RepoProfile

TARGET = "backend/app/api/routes/login.py"


def precondition(facts: FactSet, profile: RepoProfile) -> bool:
    return has_file(facts, TARGET)


def apply(repo: Path, rng: random.Random) -> MutationResult:
    return apply_patch(repo, "primary", "M-LOGSECRET")


MUTATION = Mutation(
    id="M-LOGSECRET",
    expected={"LOG-04": "fail"},
    may_affect=("LOG-02", "SEC-03"),
    generic=False,
    description="Log the issued access token in the login handler",
    precondition=precondition,
    apply=apply,
)
