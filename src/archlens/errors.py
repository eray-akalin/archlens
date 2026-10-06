"""Typed exceptions.

Stage-level errors fail a run; check-level problems become `unknown` verdicts instead of raising.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from archlens.models import LLMCallRecord


class ArchLensError(Exception):
    """Base class for every error ArchLens raises on purpose."""


class ConfigError(ArchLensError):
    """Invalid or missing configuration. `field` names the env var or YAML path at fault."""

    def __init__(self, source: str, field: str, message: str) -> None:
        super().__init__(f"{source}: {field}: {message}")
        self.source = source
        self.field = field
        self.message = message


class IngestError(ArchLensError):
    """The repository could not be cloned or snapshotted."""


class IngestLimitExceeded(IngestError):
    """The repository exceeds an `IngestLimits` bound; the run fails instead of going partial."""


class PathOutsideSnapshot(ArchLensError):
    """A path resolved outside the snapshot root (docs/SECURITY.md §4)."""


class ToolArgumentError(ArchLensError):
    """An LLM tool call can't be served as asked; the message is returned to the model."""


class StorageKeyError(ArchLensError, ValueError):
    """A run ID, artifact name, checkpoint key or cache key has an unsafe or invalid form."""


class RubricError(ArchLensError):
    """A rubric file is invalid or references an unknown rule."""


class BudgetExceeded(ArchLensError):
    """The next LLM call would cross the run budget (docs/LLM.md §9)."""


class LLMError(ArchLensError):
    """An LLM call failed after retries, or its output could not be used."""


class ProviderError(LLMError):
    """Provider error; `retryable` covers 429, 5xx, timeouts and dropped connections."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        status: int | None = None,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        self.retry_after = retry_after


class InvalidModelOutput(LLMError):
    """The model's answer doesn't validate against the response schema (or was cut off/refused)."""

    def __init__(
        self,
        message: str,
        *,
        content: str | None = None,
        records: "tuple[LLMCallRecord, ...]" = (),
    ):
        super().__init__(message)
        self.content = content
        self.records = records  # the calls that produced the unusable answer (cost was incurred)


class CassetteMiss(LLMError):
    """Replay found no recorded response: a test failure, never a network call."""
