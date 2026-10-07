"""CI configuration → `ci_workflow` and `ci_step` facts (CI-*, TEST-03).

GitHub Actions in full (triggers, permissions per workflow and job, environments, steps with
`uses` pinning); GitLab CI, Azure Pipelines and CircleCI steps from their script entries; Jenkins
`sh` steps by pattern. Step commands are redacted and capped at 500 characters. Nothing is run.
"""

import re
from typing import cast

import yaml
from pydantic import JsonValue

from archlens.facts.base import ScanContext, make_fact
from archlens.facts.extractors.common import (
    as_dict,
    as_list,
    code_evidence,
    find_line,
    is_mapping,
    is_sequence,
    text,
)
from archlens.models import Fact
from archlens.security.redact import redact

SOURCE = "ast:ci"
_SHA_PIN = re.compile(r"@[0-9a-f]{40}$")
# Checked in order: a step that builds and pushes is a deploy step.
RUN_KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "deploy",
        re.compile(
            r"(?i)\bdocker\s+push\b|\bkubectl\s+(apply|rollout|set\s+image)|\bhelm\s+(upgrade|install)"
            r"|\baz\s+(webapp|containerapp|functionapp|deployment)\b|\bazd\s+(deploy|up)\b|\bterraform\s+apply"
            r"|\bgcloud\s+(run\s+deploy|app\s+deploy)|\baws\s+(ecs|lambda|s3\s+sync|cloudformation\s+deploy)"
            r"|\b(vercel|netlify)\b.*\bdeploy|\bflyctl\s+deploy|\bnpm\s+publish|\btwine\s+upload|\bserverless\s+deploy"
            r"|webapps-deploy|amazon-ecs-deploy|build-push-action|deploy-pages|\bgh\s+release\s+create"
        ),
    ),
    (
        "test",
        re.compile(
            r"(?i)\bpytest\b|\b(npm|pnpm|yarn|bun)\s+(run\s+)?test\b|\bjest\b|\bvitest\b|\bmocha\b|\bgo\s+test\b"
            r"|\bdotnet\s+test\b|\bmvn\b.*\b(test|verify)\b|\bgradlew?\b.*\btest\b|\btox\b|\bnox\b|\bcargo\s+test\b"
            r"|\bmake\s+test\b|\bunittest\b|\bplaywright\s+test\b|\bcypress\s+run\b|\bphpunit\b|\brspec\b"
            r"|\bcoverage\s+run\b|[\w./-]*\btests?[\w-]*\.(?:sh|ps1|bat)\b"
        ),
    ),
    (
        "lint",
        re.compile(
            r"(?i)\bruff\b|\bflake8\b|\bpylint\b|\bmypy\b|\bpyright\b|\bblack\s+--check|\beslint\b|\bprettier\s+--check"
            r"|\b(npm|pnpm|yarn)\s+(run\s+)?lint\b|\bgolangci-lint\b|\bgo\s+vet\b|\bhadolint\b|\bactionlint\b"
            r"|\bdotnet\s+format\b|\btsc\b.*--noEmit|\bshellcheck\b|\bpre-commit\s+run\b|\bstylelint\b"
            r"|\bprek\s+run\b|\bbiome\s+(?:check|lint|ci)\b|pre-commit/action|golangci-lint-action"
            r"|ruff-action|super-linter"
        ),
    ),
    (
        "build",
        re.compile(
            r"(?i)\bdocker\s+(build|buildx)\b|\b(npm|pnpm|yarn)\s+(run\s+)?build\b|\bgo\s+build\b|\bdotnet\s+(build|publish)\b"
            r"|\bmvn\b.*\b(package|install)\b|\bgradlew?\b.*\b(build|assemble)\b|\bpython\s+-m\s+build\b|\buv\s+build\b"
            r"|\bcargo\s+build\b|\bmake(\s+build)?\s*$|\bpoetry\s+build\b|\btsc\b|setup-buildx-action"
            r"|\bdocker[\s-]compose\s+(?:\S+\s+)*?build\b|\bdocker\s+buildx\s+bake\b"
        ),
    ),
)


def classify_step(run: str | None, uses: str | None) -> str:
    haystack = f"{run or ''}\n{uses or ''}"
    for kind, pattern in RUN_KINDS:
        if pattern.search(haystack):
            return kind
    return "other"


def is_pinned(uses: str) -> bool:
    """Local actions and docker:// images are not third-party tags; everything else needs a SHA."""
    return uses.startswith(("./", "docker://")) or bool(_SHA_PIN.search(uses))


def _triggers(data: dict[str, object]) -> list[str]:
    on = data.get("on", data.get("True"))  # PyYAML reads the bare key `on` as boolean True
    if isinstance(on, str):
        return [on]
    if is_sequence(on):
        return [str(t) for t in as_list(on)]
    return list(as_dict(on))


def _step_fact(
    ctx: ScanContext,
    path: str,
    line: int,
    job: str,
    system: str,
    run: str | None,
    uses: str | None,
    with_keys: list[str],
) -> Fact:
    attributes: dict[str, JsonValue] = {
        "path": path,
        "system": system,
        "job": job,
        "uses": uses,
        "run": redact(run)[:500] if run else None,
        "run_kind": classify_step(run, uses),
        "pinned": is_pinned(uses) if uses else None,
        "with_keys": list[JsonValue](with_keys),
    }
    return make_fact("ci_step", SOURCE, attributes, code_evidence(ctx, path, line))


def _github(ctx: ScanContext, path: str) -> list[Fact]:
    source = text(ctx, path)
    if source is None:
        return []
    try:
        data = as_dict(yaml.safe_load(source))
        root = cast(yaml.Node | None, yaml.compose(source, Loader=yaml.SafeLoader))  # pyright: ignore[reportUnknownMemberType]
    except yaml.YAMLError:
        return []
    data = {("on" if k == "True" else k): v for k, v in data.items()}
    jobs = as_dict(data.get("jobs"))
    job_nodes = _mapping(_child(root, "jobs"))
    facts: list[Fact] = []
    for job_id, job_value in jobs.items():
        job = as_dict(job_value)
        step_nodes = _sequence(_child(job_nodes.get(job_id), "steps"))
        for index, step_value in enumerate(as_list(job.get("steps"))):
            step = as_dict(step_value)
            node = step_nodes[index] if index < len(step_nodes) else None
            # the `uses:` (else `run:`) line — what CI-05/CI-02/TEST-03 are about — else the step
            anchor = _child(node, "uses") or _child(node, "run") or node
            line = anchor.start_mark.line + 1 if anchor is not None else 1
            run = step.get("run")
            uses = step.get("uses")
            facts.append(
                _step_fact(
                    ctx,
                    path,
                    line,
                    job_id,
                    "github_actions",
                    str(run) if run is not None else None,
                    str(uses) if uses is not None else None,
                    list(as_dict(step.get("with"))),
                )
            )
    attributes: dict[str, JsonValue] = {
        "path": path,
        "system": "github_actions",
        "name": str(data.get("name", "")),
        "triggers": list[JsonValue](_triggers(data)),
        "permissions": _json(data.get("permissions")),
        "jobs": list[JsonValue](list(jobs)),
        "job_permissions": {k: _json(as_dict(v).get("permissions")) for k, v in jobs.items()},
        "environments": {k: _json(as_dict(v).get("environment")) for k, v in jobs.items()},
    }
    facts.insert(0, make_fact("ci_workflow", SOURCE, attributes, code_evidence(ctx, path, 1)))
    return facts


def _child(node: yaml.Node | None, key: str) -> yaml.Node | None:
    if isinstance(node, yaml.MappingNode):
        for k, v in node.value:
            if isinstance(k, yaml.ScalarNode) and k.value == key:
                return v
    return None


def _mapping(node: yaml.Node | None) -> dict[str, yaml.Node]:
    if not isinstance(node, yaml.MappingNode):
        return {}
    return {str(k.value): v for k, v in node.value if isinstance(k, yaml.ScalarNode)}


def _sequence(node: yaml.Node | None) -> list[yaml.Node]:
    return list(node.value) if isinstance(node, yaml.SequenceNode) else []


def _json(value: object) -> JsonValue:
    """YAML value as JSON-compatible data (strings for anything exotic)."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if is_mapping(value):
        return {k: _json(v) for k, v in as_dict(value).items()}
    if is_sequence(value):
        return [_json(v) for v in as_list(value)]
    return str(value)


_GITLAB_RESERVED = frozenset(
    {
        "stages",
        "variables",
        "image",
        "default",
        "include",
        "workflow",
        "before_script",
        "after_script",
        "cache",
        "services",
    }
)


def _command_line(ctx: ScanContext, path: str, command: str | None) -> int:
    """Line where a step's command (first 60 chars of its first line) appears; 1 if not found."""
    first = (command or "").strip().splitlines()[0][:60] if (command or "").strip() else ""
    return find_line(ctx, path, re.compile(re.escape(first))) if first else 1


def _scripted(ctx: ScanContext, path: str, system: str) -> list[Fact]:
    """GitLab / Azure Pipelines / CircleCI: every script entry becomes a step."""
    data = as_dict(next(iter(_safe_docs(ctx, path)), None))
    steps: list[tuple[str, str | None, str | None]] = []  # job, run, uses
    if system == "gitlab_ci":
        for job, body in data.items():
            if job not in _GITLAB_RESERVED and not job.startswith("."):
                steps += [(job, str(line), None) for line in as_list(as_dict(body).get("script"))]
    else:
        for job, step in _walk_steps(data, "pipeline"):
            if isinstance(step, str):  # CircleCI shorthand such as `- checkout`
                steps.append((job, None, step))
                continue
            entry = as_dict(step)
            run_value = entry.get("run")
            run = (
                entry.get("script")
                or entry.get("bash")
                or entry.get("pwsh")
                or entry.get("powershell")
            )
            run = run or (as_dict(run_value).get("command") if is_mapping(run_value) else run_value)
            task = entry.get("task")
            steps.append((job, str(run) if run else None, str(task) if task else None))
    facts = [
        _step_fact(ctx, path, _command_line(ctx, path, run or uses), job, system, run, uses, [])
        for job, run, uses in steps
    ]
    attributes: dict[str, JsonValue] = {
        "path": path,
        "system": system,
        "name": "",
        "triggers": [],
        "permissions": None,
        "jobs": list[JsonValue](sorted({job for job, _, _ in steps})),
        "job_permissions": {},
        "environments": {},
    }
    return [make_fact("ci_workflow", SOURCE, attributes, code_evidence(ctx, path, 1)), *facts]


def _safe_docs(ctx: ScanContext, path: str) -> list[object]:
    source = text(ctx, path)
    if source is None:
        return []
    try:
        return [d for d in yaml.safe_load_all(source) if d is not None]
    except yaml.YAMLError:
        return []


def _walk_steps(node: object, job: str) -> list[tuple[str, object]]:
    """(job name, step) for every `steps:` list; job names come from `job:`/`deployment:` values
    (Azure Pipelines) or `jobs:` mapping keys (CircleCI)."""
    if is_sequence(node):
        return [found for item in as_list(node) for found in _walk_steps(item, job)]
    mapping = as_dict(node)
    name = str(mapping.get("job") or mapping.get("deployment") or job)
    found = [(name, step) for step in as_list(mapping.get("steps"))]
    for key, value in mapping.items():
        if key == "steps":
            continue
        if key == "jobs" and is_mapping(value):
            for job_name, body in as_dict(value).items():
                found += _walk_steps(body, job_name)
        else:
            found += _walk_steps(value, name)
    return found


def _jenkins(ctx: ScanContext, path: str) -> list[Fact]:
    source = text(ctx, path) or ""
    facts: list[Fact] = []
    for match in re.finditer(r"""\b(?:sh|bat|pwsh)\s*\(?\s*(['"]{1,3})(.+?)\1""", source):
        line = source.count("\n", 0, match.start()) + 1
        facts.append(_step_fact(ctx, path, line, "pipeline", "jenkins", match.group(2), None, []))
    attributes: dict[str, JsonValue] = {
        "path": path, "system": "jenkins", "name": "", "triggers": [], "permissions": None,
        "jobs": ["pipeline"], "job_permissions": {}, "environments": {},
    }  # fmt: skip
    return [make_fact("ci_workflow", SOURCE, attributes, code_evidence(ctx, path, 1)), *facts]


def ci_system(path: str) -> str | None:
    if path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml")):
        return "github_actions"
    if path == ".gitlab-ci.yml":
        return "gitlab_ci"
    if path.endswith(("azure-pipelines.yml", "azure-pipelines.yaml")) or path.startswith(
        ".azure-pipelines/"
    ):
        return "azure_pipelines"
    if path == ".circleci/config.yml":
        return "circleci"
    if path.rsplit("/", 1)[-1] == "Jenkinsfile":
        return "jenkins"
    return None


def extract_ci(ctx: ScanContext) -> list[Fact]:
    facts: list[Fact] = []
    for f in ctx.snapshot.files:
        system = ci_system(f.path)
        if system is None or not f.readable:
            continue
        if system == "github_actions":
            facts += _github(ctx, f.path)
        elif system == "jenkins":
            facts += _jenkins(ctx, f.path)
        else:
            facts += _scripted(ctx, f.path, system)
    return facts
