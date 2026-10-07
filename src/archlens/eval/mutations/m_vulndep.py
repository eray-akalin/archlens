"""M-VULNDEP: pin a dependency with a known critical advisory → SEC-02 fail.

Per-ecosystem table, validated against osv-scanner on that format: Python requirements files
(`PyYAML==5.3`, CVE-2020-14343, critical — the tiny_service D02 pin). Repos in other ecosystems,
or that already have high/critical advisories, don't satisfy the precondition.
"""

import random
from pathlib import Path

from archlens.eval.mutation import Mutation, MutationResult
from archlens.eval.mutations._facts import no_severe_vulns
from archlens.eval.mutations._helpers import insert
from archlens.models import FactSet, RepoProfile

TARGET = "requirements-pinned.txt"
VULNERABLE = "PyYAML==5.3"


def precondition(facts: FactSet, profile: RepoProfile) -> bool:
    return profile.flags.get("lang_python", False) and no_severe_vulns(facts)


def apply(repo: Path, rng: random.Random) -> MutationResult:
    return MutationResult((insert(repo, TARGET, 0, [VULNERABLE]),))


MUTATION = Mutation(
    id="M-VULNDEP", expected={"SEC-02": "fail"},
    description="Pin a dependency to a version with a known critical advisory",
    precondition=precondition, apply=apply,
)  # fmt: skip
