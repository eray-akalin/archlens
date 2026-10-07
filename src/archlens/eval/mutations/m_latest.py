"""M-LATEST: change a pinned base image to `latest` → CTR-02 fail."""

import random
import re
from pathlib import Path

from archlens.eval.mutation import Mutation, MutationResult
from archlens.eval.mutations._facts import pinned_dockerfiles
from archlens.eval.mutations._helpers import MutationError, dockerfiles, read_lines, replace
from archlens.models import FactSet, RepoProfile
from archlens.rubric.rules.docker import is_pinned_image

_FROM = re.compile(r"^(\s*FROM\s+(?:--\S+\s+)*)(\S+)(.*)$", re.IGNORECASE)


def precondition(facts: FactSet, profile: RepoProfile) -> bool:
    return bool(pinned_dockerfiles(facts))


def latest(image: str) -> str:
    name = image.split("@", 1)[0]
    head, _, last = name.rpartition("/")
    last = last.split(":", 1)[0]
    return f"{head}/{last}:latest" if head else f"{last}:latest"


def apply(repo: Path, rng: random.Random) -> MutationResult:
    for rel in dockerfiles(repo):
        lines = read_lines(repo, rel)
        aliases = {
            m.group(1).lower() for line in lines if (m := re.search(r"(?i)\sAS\s+(\S+)", line))
        }
        for index, line in enumerate(lines):
            match = _FROM.match(line.rstrip("\n"))
            if not match:
                continue
            image = match.group(2)
            if (
                image.lower() in aliases
                or image == "scratch"
                or "$" in image
                or not is_pinned_image(image)
            ):
                continue
            new = f"{match.group(1)}{latest(image)}{match.group(3)}"
            return MutationResult((replace(repo, rel, index, index + 1, [new]),))
    raise MutationError("M-LATEST: no pinned external base image")


MUTATION = Mutation(
    id="M-LATEST", expected={"CTR-02": "fail"}, may_affect=("CTR-07",),
    description="Change a base image tag to latest", precondition=precondition, apply=apply,
)  # fmt: skip
