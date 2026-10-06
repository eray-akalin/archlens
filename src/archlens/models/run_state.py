"""Mutable run state, tracked by the orchestrator and exposed by the API (DATA_MODEL.md §9)."""

from typing import Literal

from pydantic import AwareDatetime

from archlens.models.base import MutableContract

StageStatus = Literal["pending", "running", "done", "failed", "skipped"]
RunStatus = Literal["queued", "running", "done", "failed"]


class StageState(MutableContract):
    stage: str
    status: StageStatus
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    error: str | None


class RunState(MutableContract):
    run_id: str
    repo_url: str | None
    ref: str | None
    status: RunStatus
    stages: list[StageState]
    metrics_done: list[str]
    cost_usd: float
