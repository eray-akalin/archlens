"""Compose services and Kubernetes workloads → `deploy_config` facts (PERF-06 limits, CTR-05
probes/healthchecks). YAML is loaded with `safe_load`; templated files (Helm) that aren't valid
YAML are skipped.
"""

import re
from pathlib import PurePosixPath

from pydantic import JsonValue

from archlens.facts.base import ScanContext, make_fact
from archlens.facts.extractors.common import (
    as_dict,
    as_list,
    code_evidence,
    find_line,
    yaml_documents,
)
from archlens.models import Fact

SOURCE = "ast:deploy"
WORKLOADS = frozenset(
    {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "CronJob", "Pod"}
)
_COMPOSE = re.compile(r"^(docker-)?compose[\w.-]*\.ya?ml$")


def is_compose(path: str) -> bool:
    return bool(_COMPOSE.match(PurePosixPath(path).name))


def _compose(ctx: ScanContext, path: str) -> list[Fact]:
    docs = yaml_documents(ctx, path)
    services = as_dict(as_dict(docs[0]).get("services")) if docs else {}
    facts: list[Fact] = []
    for name, body in services.items():
        service = as_dict(body)
        limits = as_dict(as_dict(as_dict(service.get("deploy")).get("resources")).get("limits"))
        health = service.get("healthcheck")
        attributes: dict[str, JsonValue] = {
            "path": path,
            "kind": "compose_service",
            "name": name,
            "has_limits": bool(limits)
            or any(k in service for k in ("mem_limit", "cpus", "pids_limit")),
            "has_healthcheck": health is not None and not as_dict(health).get("disable", False),
            "has_liveness": False,
            "has_readiness": False,
        }
        line = find_line(ctx, path, re.compile(rf"^\s+{re.escape(name)}\s*:"))
        facts.append(make_fact("deploy_config", SOURCE, attributes, code_evidence(ctx, path, line)))
    return facts


def _pod_spec(manifest: dict[str, object]) -> dict[str, object]:
    spec = as_dict(manifest.get("spec"))
    if manifest.get("kind") == "Pod":
        return spec
    if manifest.get("kind") == "CronJob":
        spec = as_dict(as_dict(spec.get("jobTemplate")).get("spec"))
    return as_dict(as_dict(spec.get("template")).get("spec"))


def _kubernetes(ctx: ScanContext, path: str) -> list[Fact]:
    facts: list[Fact] = []
    for doc in yaml_documents(ctx, path):
        manifest = as_dict(doc)
        kind = str(manifest.get("kind", ""))
        if kind not in WORKLOADS or "apiVersion" not in manifest:
            continue
        containers = [as_dict(c) for c in as_list(_pod_spec(manifest).get("containers"))]
        name = str(as_dict(manifest.get("metadata")).get("name", ""))
        attributes: dict[str, JsonValue] = {
            "path": path,
            "kind": "k8s_workload",
            "workload": kind,
            "name": name,
            "has_limits": bool(containers)
            and all(as_dict(c.get("resources")).get("limits") for c in containers),
            "has_liveness": any("livenessProbe" in c for c in containers),
            "has_readiness": any("readinessProbe" in c for c in containers),
            "has_healthcheck": False,
        }
        line = find_line(ctx, path, re.compile(rf"^kind:\s*{kind}\b"))
        facts.append(make_fact("deploy_config", SOURCE, attributes, code_evidence(ctx, path, line)))
    return facts


def extract_deploy_configs(ctx: ScanContext) -> list[Fact]:
    facts: list[Fact] = []
    for f in ctx.snapshot.files:
        if not f.readable or f.is_vendored or f.language != "yaml" or f.path.startswith(".github/"):
            continue
        facts += _compose(ctx, f.path) if is_compose(f.path) else _kubernetes(ctx, f.path)
    return facts
