"""Storage backends behind one set of protocols (local now, Azure in M4.2)."""

from archlens.config import Settings
from archlens.errors import ConfigError
from archlens.storage.base import (
    ArtifactStore,
    CacheStore,
    CheckpointStore,
    JobQueue,
    JobStore,
    QueueMessage,
    RunStateStore,
    Storage,
)
from archlens.storage.local import open_local_storage

__all__ = [
    "ArtifactStore",
    "CacheStore",
    "CheckpointStore",
    "JobQueue",
    "JobStore",
    "QueueMessage",
    "RunStateStore",
    "Storage",
    "open_local_storage",
    "open_storage",
]


def open_storage(settings: Settings) -> Storage:
    """The backend selected by ARCHLENS_STORAGE. Raises ConfigError for unavailable backends."""
    if settings.storage == "local":
        return open_local_storage(settings.data_dir)
    raise ConfigError("environment", "ARCHLENS_STORAGE", "the azure backend arrives in M4.2")
