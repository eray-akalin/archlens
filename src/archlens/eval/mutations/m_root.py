"""M-ROOT: drop `USER` from the final Dockerfile stage → CTR-01 fail."""

import random
from pathlib import Path

from archlens.eval.mutation import Mutation, MutationResult
from archlens.eval.mutations._facts import non_root_dockerfiles
from archlens.eval.mutations._helpers import MutationError, dockerfiles, read_lines, replace
from archlens.models import FactSet, RepoProfile


def precondition(facts: FactSet, profile: RepoProfile) -> bool:
    return bool(non_root_dockerfiles(facts))


def apply(repo: Path, rng: random.Random) -> MutationResult:
    for rel in dockerfiles(repo):
        lines = read_lines(repo, rel)
        last_from = max(
            (i for i, line in enumerate(lines) if line.lstrip().upper().startswith("FROM ")),
            default=None,
        )
        if last_from is None:
            continue
        users = [
            i for i in range(last_from, len(lines)) if lines[i].lstrip().upper().startswith("USER ")
        ]
        if not users:
            continue
        changed = [replace(repo, rel, i, i + 1, []) for i in reversed(users)]
        return MutationResult(tuple(reversed(changed)))
    raise MutationError("M-ROOT: no Dockerfile sets USER in its final stage")


MUTATION = Mutation(
    id="M-ROOT", expected={"CTR-01": "fail"}, may_affect=("CTR-07",),
    description="Drop USER from the final stage", precondition=precondition, apply=apply,
)  # fmt: skip
