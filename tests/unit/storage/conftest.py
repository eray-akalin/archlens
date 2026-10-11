"""Backend-parameterized fixtures for the storage contract suite.

`local` and `azure-fake` (the azure backend over in-memory SDK fakes) run offline. `azure` is
the real service (`pytest.mark.live`): it runs only with ARCHLENS_LIVE_TESTS=1 plus
ARCHLENS_AZURE_STORAGE_ACCOUNT and ARCHLENS_AZURE_COSMOS_ENDPOINT in the environment (never read
from `.env`), against the test-only resources from infra/modules/{storage,cosmos}.bicep — the
`contract-tests` container, the `assessments-test` queue and the `*-test` Cosmos containers, which
it empties before each test. Data-plane calls only; fractions of a cent per run.
"""

import os
from collections.abc import AsyncIterator, Callable
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


LIVE_CONTAINER, LIVE_QUEUE = "contract-tests", "assessments-test"
LIVE_COSMOS = {"runs-test": "run_id", "jobs-test": "key_id", "cache-test": "namespace"}


def _live_target() -> tuple[str, str]:
    account = os.environ.get("ARCHLENS_AZURE_STORAGE_ACCOUNT", "")
    endpoint = os.environ.get("ARCHLENS_AZURE_COSMOS_ENDPOINT", "")
    if not account or not endpoint:
        pytest.skip("set ARCHLENS_AZURE_STORAGE_ACCOUNT and ARCHLENS_AZURE_COSMOS_ENDPOINT")
    return account, endpoint


def _reset_live(account: str, endpoint: str) -> None:
    """Empty the test container, queue and Cosmos containers (sync SDK, before the test)."""
    from azure.cosmos import CosmosClient
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient
    from azure.storage.queue import QueueClient

    credential = DefaultAzureCredential()
    blobs = BlobServiceClient(f"https://{account}.blob.core.windows.net", credential=credential)
    container = blobs.get_container_client(LIVE_CONTAINER)
    for blob in container.list_blobs():
        container.delete_blob(blob.name)
    QueueClient(
        f"https://{account}.queue.core.windows.net", LIVE_QUEUE, credential=credential
    ).clear_messages()
    database = CosmosClient(endpoint, credential=credential).get_database_client(azure.DATABASE)
    for name, key in LIVE_COSMOS.items():
        items = database.get_container_client(name)
        query = f"SELECT c.id, c.{key} AS pk FROM c"
        for item in items.query_items(query, enable_cross_partition_query=True):
            items.delete_item(item["id"], partition_key=item["pk"])


def _azure_live(tmp_path: Path, clock: FakeClock) -> StorageFactory:
    """`archlens.storage.azure` against the real test-only resources (aio clients, managed or
    developer identity via DefaultAzureCredential)."""
    from azure.cosmos.aio import CosmosClient
    from azure.identity.aio import DefaultAzureCredential
    from azure.storage.blob.aio import BlobServiceClient
    from azure.storage.queue.aio import QueueClient

    account, endpoint = _live_target()
    _reset_live(account, endpoint)

    def make() -> Storage:
        credential = DefaultAzureCredential()
        blob_service = BlobServiceClient(
            f"https://{account}.blob.core.windows.net", credential=credential
        )
        container = blob_service.get_container_client(LIVE_CONTAINER)
        queue = QueueClient(
            f"https://{account}.queue.core.windows.net", LIVE_QUEUE, credential=credential
        )
        cosmos = CosmosClient(endpoint, credential=credential)
        database = cosmos.get_database_client(azure.DATABASE)

        async def close() -> None:
            await blob_service.close()
            await queue.close()
            await cosmos.close()
            await credential.close()

        return Storage(
            artifacts=azure.BlobArtifactStore(container),
            run_state=azure.CosmosRunStateStore(database.get_container_client("runs-test")),
            cache=azure.CosmosCacheStore(database.get_container_client("cache-test"), clock),
            checkpoints=azure.BlobCheckpointStore(container),
            jobs=azure.CosmosJobStore(database.get_container_client("jobs-test")),
            queue=azure.StorageQueueJobQueue(queue),
            close=close,
        )

    return make


BACKENDS: dict[str, Callable[[Path, FakeClock], StorageFactory]] = {
    "local": _local,
    "azure-fake": _azure_fake,
    "azure": _azure_live,
}
BACKEND_PARAMS = [
    pytest.param("local"),
    pytest.param("azure-fake"),
    pytest.param("azure", marks=pytest.mark.live),
]


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(params=BACKEND_PARAMS)
async def make_storage(
    request: pytest.FixtureRequest, tmp_path: Path, clock: FakeClock
) -> AsyncIterator[StorageFactory]:
    """Each call opens a new Storage over the same backing location (to test persistence);
    every one is closed after the test."""
    factory = BACKENDS[request.param](tmp_path, clock)
    opened: list[Storage] = []

    def make() -> Storage:
        opened.append(factory())
        return opened[-1]

    yield make
    for storage in opened:
        await storage.aclose()


@pytest.fixture
def backend(request: pytest.FixtureRequest) -> str:
    return str(request.node.callspec.params["make_storage"])  # pyright: ignore[reportAttributeAccessIssue]


@pytest.fixture
def storage(make_storage: StorageFactory) -> Storage:
    return make_storage()
