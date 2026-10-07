"""M-UNPIN: replace third-party action pins with `@main` → CI-05 fail."""

import random
import re
from pathlib import Path

from archlens.eval.mutation import ChangedLines, Mutation, MutationResult
from archlens.eval.mutations._facts import ALLOWED_ACTION_OWNERS, all_actions_pinned
from archlens.eval.mutations._helpers import MutationError, read_lines, replace, workflows
from archlens.models import FactSet, RepoProfile

_USES = re.compile(r"^(\s*-?\s*uses:\s*['\"]?)([\w.-]+/[^@\s'\"#]+)@([^\s'\"#]+)(.*)$")


def precondition(facts: FactSet, profile: RepoProfile) -> bool:
    return all_actions_pinned(facts)


def apply(repo: Path, rng: random.Random) -> MutationResult:
    for rel in workflows(repo):
        changed: list[ChangedLines] = []
        for index, line in enumerate(read_lines(repo, rel)):
            match = _USES.match(line.rstrip("\n"))
            if not match or match.group(2).split("/", 1)[0].lower() in ALLOWED_ACTION_OWNERS:
                continue
            comment = match.group(4).split("#", 1)[0].rstrip()  # drop the `# v1.2.3` pin note
            new = f"{match.group(1)}{match.group(2)}@main{comment}"
            changed.append(replace(repo, rel, index, index + 1, [new]))
        if changed:
            return MutationResult(tuple(changed))
    raise MutationError("M-UNPIN: no third-party actions to unpin")


MUTATION = Mutation(
    id="M-UNPIN", expected={"CI-05": "fail"}, may_affect=("CI-03",),
    description="Replace SHA/tag pins with @main", precondition=precondition, apply=apply,
)  # fmt: skip
