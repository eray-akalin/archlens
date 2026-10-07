"""`docker.*` rules over `dockerfile`, `deploy_config` and `hadolint_finding` facts (CTR-01..07).

Per-Dockerfile checks (non-root, multi-stage) aggregate as: every Dockerfile complies → pass,
some → partial, none → fail. NA when the repository has no Dockerfile (applicability is also in
the rubric).
"""

from collections.abc import Callable

from pydantic import Field

from archlens.models import Fact, RuleOutcome
from archlens.rubric.context import RuleContext
from archlens.rubric.registry import RuleParams, rule
from archlens.rubric.rules._common import (
    MAX_EVIDENCE,
    attr_bool,
    attr_int,
    attr_list,
    attr_str,
    fact_evidence,
    missing_data,
    plural,
    scan,
)

TOOL = "ast:docker"
DEPLOY_TOOL = "ast:deploy"
ROOT_USERS = frozenset({"root", "0"})


def _dockerfiles(ctx: RuleContext) -> RuleOutcome | list[Fact]:
    if (missing := missing_data(ctx, TOOL)) is not None:
        return missing
    files = ctx.facts.by_kind("dockerfile")
    if not files:
        return RuleOutcome(
            verdict="not_applicable", claim="No Dockerfile found.",
            evidence=[scan(ctx, TOOL, "Dockerfiles", 0)], reason="no_dockerfile",
        )  # fmt: skip
    return files


def _per_file(
    ctx: RuleContext, files: list[Fact], complies: Callable[[Fact], bool], what: str, why: str
) -> RuleOutcome:
    bad = [f for f in files if not complies(f)]
    evidence = [
        scan(ctx, TOOL, f"Dockerfiles without {what}", len(bad)),
        *fact_evidence(bad or files, MAX_EVIDENCE - 1),
    ]
    if not bad:
        return RuleOutcome(
            verdict="pass",
            claim=f"All {plural(len(files), 'Dockerfile')} {why}.",
            evidence=evidence,
        )
    paths = ", ".join(attr_str(f, "path") or "?" for f in bad[:3])
    return RuleOutcome(
        verdict="partial" if len(bad) < len(files) else "fail",
        claim=f"Missing {what} in {len(bad)} of {plural(len(files), 'Dockerfile')}: {paths}.",
        evidence=evidence,
    )


class NoParams(RuleParams):
    pass


def runs_as_non_root(dockerfile: Fact) -> bool:
    user = (attr_str(dockerfile, "user") or "").strip()
    name = user.split(":", 1)[0]
    return bool(user) and name not in ROOT_USERS


@rule("docker.non_root")
def non_root(ctx: RuleContext, params: NoParams) -> RuleOutcome:
    """The final stage of every Dockerfile sets a `USER` that is not root/0."""
    files = _dockerfiles(ctx)
    if isinstance(files, RuleOutcome):
        return files
    return _per_file(
        ctx, files, runs_as_non_root, "a non-root final USER", "run as a non-root USER"
    )


def is_pinned_image(image: str) -> bool:
    """Digest, or a tag other than `latest`; images built from variables can't be judged and
    count as pinned."""
    if "$" in image or image == "scratch" or "@" in image:
        return True
    last = image.rsplit("/", 1)[-1]
    return ":" in last and last.rsplit(":", 1)[1] != "latest"


@rule("docker.pinned_base")
def pinned_base(ctx: RuleContext, params: NoParams) -> RuleOutcome:
    """fail if any external base image has no tag or uses `latest`."""
    files = _dockerfiles(ctx)
    if isinstance(files, RuleOutcome):
        return files
    unpinned = [
        (f, str(image)) for f in files for image in attr_list(f, "external_bases")
        if not is_pinned_image(str(image))
    ]  # fmt: skip
    if not unpinned:
        return RuleOutcome(
            verdict="pass",
            claim=f"Every base image in {plural(len(files), 'Dockerfile')} has a tag or digest.",
            evidence=[
                scan(ctx, TOOL, "unpinned base images", 0),
                *fact_evidence(files, MAX_EVIDENCE - 1),
            ],
        )
    images = ", ".join(sorted({image for _, image in unpinned})[:3])
    return RuleOutcome(
        verdict="fail",
        claim=f"Unpinned base image(s): {images}.",
        evidence=[
            scan(ctx, TOOL, "unpinned base images", len(unpinned)),
            *fact_evidence([f for f, _ in unpinned], MAX_EVIDENCE - 1),
        ],
    )


@rule("docker.multistage")
def multistage(ctx: RuleContext, params: NoParams) -> RuleOutcome:
    """Every Dockerfile has more than one stage."""
    files = _dockerfiles(ctx)
    if isinstance(files, RuleOutcome):
        return files
    return _per_file(
        ctx,
        files,
        lambda f: attr_int(f, "stages") > 1,
        "a multi-stage build",
        "use multi-stage builds",
    )


@rule("docker.healthcheck")
def healthcheck(ctx: RuleContext, params: NoParams) -> RuleOutcome:
    """A Dockerfile HEALTHCHECK, or a compose healthcheck / k8s liveness or readiness probe."""
    for tool in (TOOL, DEPLOY_TOOL):
        run = ctx.tool_run(tool)
        if run is None or run.status in ("error", "timeout"):
            return RuleOutcome(
                verdict="unknown", claim=f"{tool} could not run.", evidence=[],
                reason=f"tool_{run.status}" if run else "tool_not_run",
            )  # fmt: skip
    dockerfiles = ctx.facts.by_kind("dockerfile")
    configs = ctx.facts.by_kind("deploy_config")
    if not dockerfiles and not configs:
        return RuleOutcome(
            verdict="not_applicable", claim="No Dockerfile, compose file or Kubernetes workload.",
            evidence=[], reason="no_container_config",
        )  # fmt: skip
    found = [f for f in dockerfiles if attr_bool(f, "has_healthcheck")] + [
        c for c in configs
        if any(attr_bool(c, k) for k in ("has_healthcheck", "has_liveness", "has_readiness"))
    ]  # fmt: skip
    query = "HEALTHCHECK instructions, compose healthchecks, k8s probes"
    where = {attr_str(f, "path") or "?" for f in found}
    if found:
        return RuleOutcome(
            verdict="pass",
            claim=f"Health checks defined in {', '.join(sorted(where)[:3])}.",
            evidence=[scan(ctx, TOOL, query, len(found)), *fact_evidence(found, MAX_EVIDENCE - 1)],
        )
    return RuleOutcome(
        verdict="fail",
        claim="No HEALTHCHECK, compose healthcheck or Kubernetes probe is defined.",
        evidence=[
            scan(ctx, TOOL, query, 0),
            *fact_evidence(dockerfiles + configs, MAX_EVIDENCE - 1),
        ],
    )


class HadolintParams(RuleParams):
    max_errors: int = Field(default=0, ge=0)


@rule("docker.hadolint_errors")
def hadolint_errors(ctx: RuleContext, params: HadolintParams) -> RuleOutcome:
    """hadolint findings at error level (severity high) ≤ `max_errors`."""
    if (missing := missing_data(ctx, "hadolint")) is not None:
        return missing
    errors = [f for f in ctx.facts.by_kind("hadolint_finding") if f.severity == "high"]
    evidence = [
        scan(ctx, "hadolint", "error-level findings", len(errors)),
        *fact_evidence(errors, MAX_EVIDENCE - 1),
    ]
    if len(errors) <= params.max_errors:
        return RuleOutcome(
            verdict="pass",
            claim=f"hadolint reported {plural(len(errors), 'error')} "
            f"(allowed: {params.max_errors}).",
            evidence=evidence,
        )
    codes = sorted({attr_str(f, "code") or "?" for f in errors})
    return RuleOutcome(
        verdict="fail",
        claim=f"hadolint reported {plural(len(errors), 'error')} ({', '.join(codes[:4])}).",
        evidence=evidence,
    )
