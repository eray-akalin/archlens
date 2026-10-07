"""Combined variants (`eval/variants.yaml`, EVALUATION.md §2.1) and their constraints.

An entry is a mutation id, or `"M-X + I-Y"`: an injection paired with the defect it sits next to.
Static rules (checked before anything runs): unknown ids; (1) no two mutations with expected checks
in the same metric; (3) `M-SECRET` and `M-LOGSECRET` never together; (4) `M-NOTESTS` never with a
CI mutation. Rule (2) — no two mutations edit the same file, except a declared injection pair — is
checked when the variant is applied, from the files each mutation actually changed.
"""

import random
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import Field, ValidationError, model_validator

from archlens.errors import ArchLensError, ConfigError
from archlens.eval.mutation import Mutation, MutationResult
from archlens.models import Contract

DEFAULT_VARIANTS_FILE = Path("eval/variants.yaml")
NEVER_TOGETHER = (("M-SECRET", "M-LOGSECRET"),)
NO_CI_WITH = "M-NOTESTS"
CI_METRIC = "cicd"


class VariantError(ArchLensError):
    """A variant breaks a constraint or can't be applied."""


class VariantSpec(Contract):
    id: Annotated[str, Field(pattern=r"^V[A-Z0-9]*\d+$")]
    repo: str
    mutations: list[str] = Field(default_factory=list[str])
    all_generic: bool = False  # VX1: every generic mutation whose precondition holds

    @model_validator(mode="after")
    def _has_mutations(self) -> "VariantSpec":
        if not self.mutations and not self.all_generic:
            raise ValueError(f"{self.id}: needs mutations or all_generic")
        return self

    def entries(self) -> list[tuple[str, ...]]:
        """Each entry as a group: `("M-SQLI",)` or `("M-SQLI", "I-COMMENT")`."""
        return [tuple(part.strip() for part in item.split("+")) for item in self.mutations]

    def mutation_ids(self) -> list[str]:
        return [m for group in self.entries() for m in group]


class VariantsFile(Contract):
    variants: list[VariantSpec]

    @model_validator(mode="after")
    def _unique(self) -> "VariantsFile":
        ids = [v.id for v in self.variants]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate variant ids: {ids}")
        return self


def load_variants(path: Path = DEFAULT_VARIANTS_FILE) -> list[VariantSpec]:
    try:
        return VariantsFile.model_validate(
            yaml.safe_load(path.read_text(encoding="utf-8"))
        ).variants
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise ConfigError(str(path), "variants", str(exc)) from exc


def static_problems(
    variant: VariantSpec, mutations: Mapping[str, Mutation], check_metric: Mapping[str, str]
) -> list[str]:
    """Rules 1, 3, 4 and unknown ids (rule 2 needs the applied edits)."""
    ids = variant.mutation_ids()
    problems = [f"{variant.id}: unknown mutation {m}" for m in ids if m not in mutations]
    known = [mutations[m] for m in ids if m in mutations]
    for group in variant.entries():
        injections = [m for m in group if m.startswith("I-")]
        if len(group) > 1 and len(injections) != len(group) - 1:
            problems.append(
                f"{variant.id}: a pair is one defect plus injections: {' + '.join(group)}"
            )
    owners: dict[str, str] = {}
    for mutation in known:
        for metric in sorted({check_metric.get(c, "?") for c in mutation.expected}):
            if metric in owners:
                problems.append(
                    f"{variant.id}: {owners[metric]} and {mutation.id} both target "
                    f"{metric} (rule 1)"
                )
            owners.setdefault(metric, mutation.id)
    for a, b in NEVER_TOGETHER:
        if a in ids and b in ids:
            problems.append(f"{variant.id}: {a} and {b} can't share a variant (rule 3)")
    if NO_CI_WITH in ids:
        ci = [
            m.id
            for m in known
            if m.id != NO_CI_WITH and any(check_metric.get(c) == CI_METRIC for c in m.expected)
        ]
        if ci:
            problems.append(f"{variant.id}: {NO_CI_WITH} can't be combined with {ci} (rule 4)")
    return problems


@dataclass(frozen=True)
class AppliedMutation:
    mutation: Mutation
    result: MutationResult


def apply_variant(
    variant: VariantSpec, repo: Path, mutations: Mapping[str, Mutation], seed: int
) -> list[AppliedMutation]:
    """Apply the variant's mutations in order to `repo` (a fresh copy). Raises VariantError when
    two mutations outside a declared pair edit the same file (rule 2)."""
    rng = random.Random(seed)
    applied: list[AppliedMutation] = []
    pair_of: dict[str, int] = {}
    for index, group in enumerate(variant.entries()):
        for mutation_id in group:
            pair_of[mutation_id] = index
    touched: dict[str, str] = {}
    for mutation_id in variant.mutation_ids():
        mutation = mutations[mutation_id]
        result = mutation.apply(repo, rng)
        for path in sorted(result.files):
            other = touched.get(path)
            if other is not None and pair_of[other] != pair_of[mutation_id]:
                raise VariantError(
                    f"{variant.id}: {other} and {mutation_id} both edit {path} (rule 2)"
                )
            touched.setdefault(path, mutation_id)
        applied.append(AppliedMutation(mutation, result))
    return applied
