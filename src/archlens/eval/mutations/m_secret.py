"""M-SECRET: add a generated private-key block to a config file → SEC-01 fail."""

import random
from pathlib import Path

from archlens.eval.mutation import Mutation, MutationResult
from archlens.eval.mutations._facts import no_secrets
from archlens.eval.mutations._helpers import generated_private_key, insert
from archlens.models import FactSet, RepoProfile

TARGET = "config/deploy-key.pem"


def precondition(facts: FactSet, profile: RepoProfile) -> bool:
    return no_secrets(facts)


def apply(repo: Path, rng: random.Random) -> MutationResult:
    return MutationResult((insert(repo, TARGET, 0, generated_private_key(rng)),))


MUTATION = Mutation(
    id="M-SECRET", expected={"SEC-01": "fail"},
    description="Add a generated private-key block to a config file",
    precondition=precondition, apply=apply,
)  # fmt: skip
