"""The mutation contract (EVALUATION.md §2): a known defect injected into a copy of a base repo.

Mutations are deterministic given the seed; preconditions are fact-based, never model-based.
Injection mutations (`I-*`) carry no expected verdicts. The 13 + 4 mutations live in
`archlens.eval.mutations` (M3.4); everything else only depends on this contract.
"""

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from archlens.models import FactSet, RepoProfile, Verdict


@dataclass(frozen=True)
class ChangedLines:
    path: str  # repo-relative POSIX
    start_line: int  # after the edit, 1-based inclusive
    end_line: int


@dataclass(frozen=True)
class MutationResult:
    changed: tuple[ChangedLines, ...]

    @property
    def files(self) -> frozenset[str]:
        return frozenset(c.path for c in self.changed)


Precondition = Callable[[FactSet, RepoProfile], bool]
Apply = Callable[[Path, random.Random], MutationResult]


@dataclass(frozen=True)
class Mutation:
    id: str  # "M-ROOT", "I-README"
    expected: dict[str, Verdict]  # target check → expected verdict ({} for injections)
    description: str
    precondition: Precondition
    apply: Apply
    may_affect: tuple[str, ...] = ()
    generic: bool = True  # False → needs eval/patches/<repo_id>/<id>.patch
    absence: bool = False  # located recall counts via the verdict alone (e.g. deleted tests)
    tags: tuple[str, ...] = field(default=())

    @property
    def injection(self) -> bool:
        return self.id.startswith("I-")
