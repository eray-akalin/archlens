"""Mutation registry (EVALUATION.md §2, §5). The 13 defect and 4 injection mutations arrive in
M3.4; until then the registry is empty and only base runs can be planned."""

from archlens.eval.mutation import Mutation

MUTATIONS: dict[str, Mutation] = {}


def registry() -> dict[str, Mutation]:
    return dict(MUTATIONS)
