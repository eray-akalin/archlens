"""Hosted-API job contracts (DATA_MODEL.md §10)."""

from typing import Annotated

from pydantic import AwareDatetime, Field

from archlens.models.base import Contract

MetricName = Annotated[str, Field(min_length=1, max_length=40)]


class AssessmentRequest(Contract):
    """Body of `POST /assessments`. The URL is checked by the URL policy before anything else."""

    repo_url: Annotated[str, Field(min_length=1, max_length=300)]
    ref: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    metrics: Annotated[list[MetricName], Field(max_length=20)] | None = None


class AssessmentAccepted(Contract):
    run_id: str


class Job(Contract):
    """One queued assessment. `key_id` is a fingerprint of the API key, never the key itself."""

    run_id: str
    key_id: str
    repo_url: str  # normalized by the URL policy
    ref: str | None
    metrics: list[str] | None
    created_at: AwareDatetime
