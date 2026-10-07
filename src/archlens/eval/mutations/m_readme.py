"""M-README: truncate the README to its title → DOC-01 fail."""

import random
from pathlib import Path

from archlens.eval.mutation import Mutation, MutationResult
from archlens.eval.mutations._facts import readme_lines
from archlens.eval.mutations._helpers import MutationError, files, read_lines, replace
from archlens.models import FactSet, RepoProfile


def precondition(facts: FactSet, profile: RepoProfile) -> bool:
    return readme_lines(facts) > 3


def top_readme(repo: Path) -> str:
    found = sorted(
        (p for p in files(repo) if "/" not in p and p.upper().startswith("README")), key=len
    )
    if not found:
        raise MutationError("no top-level README")
    return found[0]


def apply(repo: Path, rng: random.Random) -> MutationResult:
    rel = top_readme(repo)
    lines = read_lines(repo, rel)
    title = next((line for line in lines if line.strip()), "# README\n")
    return MutationResult((replace(repo, rel, 0, len(lines), [title.rstrip("\n")]),))


MUTATION = Mutation(
    id="M-README", expected={"DOC-01": "fail"}, may_affect=("DOC-06",), absence=True,
    description="Truncate the README to its title", precondition=precondition, apply=apply,
)  # fmt: skip
