"""Backend-parameterized fixtures for the storage contract suite.

`local` and `azure-fake` (the azure backend over in-memory SDK fakes) run offline; the live
Azure entry (`pytest.mark.live`) is added once the M4.4 resources exist.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from archlens.storage import Storage, azure, open_local_storage
from tests.unit.storage.azure_fakes import FakeBlobContainer, FakeCosmosContainer, FakeQueue

StorageFactory = Callable[[], Storage]


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def _local(tmp_path: Path, clock: FakeClock) -> StorageFactory:
    return lambda: open_local_storage(tmp_path / "data", clock=clock)


def _azure_fake(tmp_path: Path, clock: FakeClock) -> StorageFactory:
    """`archlens.storage.azure` over in-memory SDK fakes (shared across factory calls, so
    reopening sees the same data)."""
    blobs, queue = FakeBlobContainer(), FakeQueue(clock)
    runs, jobs, cache = (
        FakeCosmosContainer("run_id"), FakeCosmosContainer("key_id"),
        FakeCosmosContainer("namespace"),
    )  # fmt: skip

    def make() -> Storage:
        return Storage(
            artifacts=azure.BlobArtifactStore(cast(Any, blobs)),
            run_state=azure.CosmosRunStateStore(cast(Any, runs)),
            cache=azure.CosmosCacheStore(cast(Any, cache), clock),
            checkpoints=azure.BlobCheckpointStore(cast(Any, blobs)),
            jobs=azure.CosmosJobStore(cast(Any, jobs)),
            queue=azure.StorageQueueJobQueue(cast(Any, queue)),
        )

    return make


BACKENDS: dict[str, Callable[[Path, FakeClock], StorageFactory]] = {
    "local": _local,
    "azure-fake": _azure_fake,
}
BACKEND_PARAMS = [pytest.param("local"), pytest.param("azure-fake")]


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(params=BACKEND_PARAMS)
def make_storage(
    request: pytest.FixtureRequest, tmp_path: Path, clock: FakeClock
) -> StorageFactory:
    """Each call opens a new Storage over the same backing location (to test persistence)."""
    return BACKENDS[request.param](tmp_path, clock)


@pytest.fixture
def storage(make_storage: StorageFactory) -> Storage:
    return make_storage()
