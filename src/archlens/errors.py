"""Typed exceptions.

Stage-level errors fail a run; check-level problems become `unknown` verdicts instead of raising.
"""


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


class StorageKeyError(ArchLensError, ValueError):
    """A run ID, artifact name, checkpoint key or cache key has an unsafe or invalid form."""


class RubricError(ArchLensError):
    """A rubric file is invalid or references an unknown rule."""


class BudgetExceeded(ArchLensError):
    """The next LLM call would cross the run budget (docs/LLM.md §9)."""
