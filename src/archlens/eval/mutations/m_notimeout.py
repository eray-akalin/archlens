"""M-NOTIMEOUT: outbound http call without a timeout → PERF-05 fail (patch, `primary` only)."""

import random
from pathlib import Path

from archlens.eval.mutation import Mutation, MutationResult
from archlens.eval.mutations._facts import has_file
from archlens.eval.mutations._helpers import apply_patch
from archlens.models import FactSet, RepoProfile

TARGET = "backend/app/api/routes/utils.py"


def precondition(facts: FactSet, profile: RepoProfile) -> bool:
    return has_file(facts, TARGET)


def apply(repo: Path, rng: random.Random) -> MutationResult:
    return apply_patch(repo, "primary", "M-NOTIMEOUT")


MUTATION = Mutation(
    id="M-NOTIMEOUT",
    expected={"PERF-05": "fail"},
    may_affect=(),
    generic=False,
    description="Outbound HTTP call without a timeout",
    precondition=precondition,
    apply=apply,
)
