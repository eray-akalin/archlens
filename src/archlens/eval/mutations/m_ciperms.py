"""M-CIPERMS: give a workflow job `permissions: write-all` → CI-04 fail."""

import random
from pathlib import Path

import yaml

from archlens.eval.mutation import Mutation, MutationResult
from archlens.eval.mutations._facts import all_workflows_restricted
from archlens.eval.mutations._helpers import (
    MutationError,
    child,
    insert,
    replace,
    workflows,
    yaml_root,
)
from archlens.models import FactSet, RepoProfile


def precondition(facts: FactSet, profile: RepoProfile) -> bool:
    return all_workflows_restricted(facts)


def apply(repo: Path, rng: random.Random) -> MutationResult:
    for rel in workflows(repo):
        jobs = child(yaml_root(repo, rel), "jobs")
        if jobs is None or not isinstance(jobs[1], yaml.MappingNode) or not jobs[1].value:
            continue
        job_key, job = jobs[1].value[0]
        if not isinstance(job, yaml.MappingNode) or not job.value:
            continue
        indent = " " * job.value[0][0].start_mark.column
        existing = child(job, "permissions")
        if existing is not None:
            key, value = existing
            end = value.end_mark.line if value.end_mark.column == 0 else value.end_mark.line + 1
            change = replace(
                repo, rel, key.start_mark.line, end, [f"{indent}permissions: write-all"]
            )
        else:
            change = insert(
                repo, rel, job_key.start_mark.line + 1, [f"{indent}permissions: write-all"]
            )
        if yaml_root(repo, rel) is None:
            raise MutationError(f"M-CIPERMS: {rel} is no longer valid YAML")
        return MutationResult((change,))
    raise MutationError("M-CIPERMS: no GitHub workflow with jobs")


MUTATION = Mutation(
    id="M-CIPERMS", expected={"CI-04": "fail"}, may_affect=("CI-03",),
    description="Set permissions: write-all", precondition=precondition, apply=apply,
)  # fmt: skip
