"""Assessment routes (ARCHITECTURE.md §6, without `/ask`).

Every route but `/healthz` needs a valid `X-API-Key`. A run is visible only to the key that
created it; anything else — another key's run, a malformed id, an artifact the run didn't produce —
is a plain 404, so the API never confirms that someone else's run exists.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from ulid import ULID

from archlens.api.auth import HEADER, ApiKeys
from archlens.errors import IngestError, StorageKeyError
from archlens.ingest.clone import validate_ref
from archlens.models import AssessmentAccepted, AssessmentRequest, Job, RunState
from archlens.orchestrator.pipeline import ARTIFACTS, queued_state
from archlens.security.url_policy import UrlPolicyError, check_repo_url
from archlens.storage import Storage
from archlens.storage.base import check_run_id

REPORT = "assessment.json"
ACTIVE = ("queued", "running")
CONTENT_TYPES = {
    ".json": "application/json",
    ".md": "text/markdown; charset=utf-8",
    ".html": "text/html; charset=utf-8",
}
# Reports carry repository text: no scripts, no remote loads, no MIME sniffing.
HTML_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:",
    "X-Content-Type-Options": "nosniff",
}


@dataclass(frozen=True)
class ApiState:
    storage: Storage
    keys: ApiKeys
    allowed_hosts: tuple[str, ...]
    max_concurrent: int  # runs queued or running per key
    runs_per_day: int  # per key, over the last 24 hours
    metrics: frozenset[str]  # known rubric names
    clock: Callable[[], datetime]


router = APIRouter()


def api_state(request: Request) -> ApiState:
    return cast(ApiState, request.app.state.archlens)


def require_key(
    request: Request, x_api_key: Annotated[str | None, Header(alias=HEADER)] = None
) -> str:
    owner = api_state(request).keys.authenticate(x_api_key)
    if owner is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing or invalid API key")
    return owner


Owner = Annotated[str, Depends(require_key)]
State = Annotated[ApiState, Depends(api_state)]


async def owned_job(api: ApiState, owner: str, run_id: str) -> Job:
    try:
        check_run_id(run_id)
    except StorageKeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such run") from None
    job = await api.storage.jobs.get(run_id)
    if job is None or job.key_id != owner:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such run")
    return job


@router.post("/assessments", status_code=status.HTTP_202_ACCEPTED)
async def create_assessment(
    body: AssessmentRequest, owner: Owner, api: State
) -> AssessmentAccepted:
    """Queue an assessment. 400: URL policy, ref or metrics; 429: quota."""
    try:
        repo_url = check_repo_url(body.repo_url, api.allowed_hosts)
        validate_ref(body.ref)
    except (UrlPolicyError, IngestError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    unknown = sorted(set(body.metrics or ()) - api.metrics)
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown metrics: {unknown}")
    now = api.clock()
    recent = await api.storage.jobs.by_key(owner, now - timedelta(days=1))
    if len(recent) >= api.runs_per_day:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, f"daily quota of {api.runs_per_day} runs reached"
        )
    active = [
        job.run_id for job in recent
        if (state := await api.storage.run_state.load(job.run_id)) is not None
        and state.status in ACTIVE
    ]  # fmt: skip
    if len(active) >= api.max_concurrent:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"{len(active)} run(s) still in progress (limit {api.max_concurrent}): {active[0]}",
        )
    run_id = str(ULID())
    metrics = sorted(set(body.metrics)) if body.metrics else None
    job = Job(
        run_id=run_id, key_id=owner, repo_url=repo_url, ref=body.ref, metrics=metrics,
        created_at=now,
    )  # fmt: skip
    await api.storage.jobs.create(job)
    await api.storage.run_state.save(queued_state(run_id, repo_url, body.ref))
    await api.storage.queue.send(run_id)
    return AssessmentAccepted(run_id=run_id)


@router.get("/assessments/{run_id}")
async def get_assessment(run_id: str, owner: Owner, api: State) -> RunState:
    """Status and stage progress."""
    await owned_job(api, owner, run_id)
    state = await api.storage.run_state.load(run_id)
    if state is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such run")
    return state


@router.get("/assessments/{run_id}/report")
async def get_report(run_id: str, owner: Owner, api: State) -> Response:
    """The `AssessmentReport` JSON; 404 until the run has produced it."""
    await owned_job(api, owner, run_id)
    data = await api.storage.artifacts.get(run_id, REPORT)
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "the run has not produced a report")
    return Response(content=data, media_type="application/json")


@router.get("/assessments/{run_id}/artifacts/{name}")
async def get_artifact(run_id: str, name: str, owner: Owner, api: State) -> Response:
    """An artifact the run produced; 404 for any other name."""
    await owned_job(api, owner, run_id)
    if name not in ARTIFACTS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such artifact")
    data = await api.storage.artifacts.get(run_id, name)
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "the run has not produced this artifact")
    suffix = name[name.rfind(".") :]
    headers = HTML_HEADERS if suffix == ".html" else {"X-Content-Type-Options": "nosniff"}
    return Response(content=data, media_type=CONTENT_TYPES[suffix], headers=headers)
