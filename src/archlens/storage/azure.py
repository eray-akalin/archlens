"""Azure backend (M4.2): Blob for artifacts and checkpoints, Cosmos DB for run state, jobs and the
cache, a Storage Queue for jobs. Data-plane access only, with the workload's managed identity
(`DefaultAzureCredential`); no account keys (SECURITY.md §7).

Layout::

    blob   artifacts/runs/<run_id>/artifacts/<name>
    blob   artifacts/runs/<run_id>/checkpoints/<key>.json
    cosmos archlens/runs   {id: run_id, run_id, state}                      partition /run_id
    cosmos archlens/jobs   {id: run_id, key_id, created_ts, job}            partition /key_id
    cosmos archlens/cache  {id: sha256(namespace, key), namespace, value, expires_at, ttl}
                                                                             partition /namespace
    queue  assessments     message text = run_id

Each store takes the SDK client it uses, so the contract suite also runs against in-memory fakes.
"""

import base64
import hashlib
import time
from collections.abc import Callable
from datetime import datetime
from typing import cast

from azure.core.exceptions import ResourceNotFoundError
from azure.cosmos.aio import ContainerProxy, CosmosClient
from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob.aio import BlobServiceClient, ContainerClient
from azure.storage.queue.aio import QueueClient

from archlens.config import Settings
from archlens.errors import ConfigError
from archlens.models import Job, RunState
from archlens.storage.base import (
    QueueMessage,
    Storage,
    check_artifact_name,
    check_cache_key,
    check_checkpoint_key,
    check_key_id,
    check_run_id,
)

Clock = Callable[[], float]
BLOB_CONTAINER = "artifacts"
DATABASE = "archlens"
RUNS, JOBS, CACHE = "runs", "jobs", "cache"
_CHECKPOINT_SUFFIX = ".json"
Document = dict[str, object]


def _prefix(run_id: str, kind: str) -> str:
    return f"runs/{check_run_id(run_id)}/{kind}/"


async def _download(container: ContainerClient, name: str) -> bytes | None:
    try:
        downloader = await container.download_blob(name)
        return await downloader.readall()
    except ResourceNotFoundError:
        return None


async def _names(container: ContainerClient, prefix: str) -> list[str]:
    return sorted([blob.name[len(prefix) :] async for blob in container.list_blobs(prefix)])


class BlobArtifactStore:
    def __init__(self, container: ContainerClient) -> None:
        self._container = container

    async def put(self, run_id: str, name: str, data: bytes) -> None:
        blob = _prefix(run_id, "artifacts") + check_artifact_name(name)
        await self._container.upload_blob(blob, data, overwrite=True)

    async def get(self, run_id: str, name: str) -> bytes | None:
        return await _download(
            self._container, _prefix(run_id, "artifacts") + check_artifact_name(name)
        )

    async def names(self, run_id: str) -> list[str]:
        return await _names(self._container, _prefix(run_id, "artifacts"))


class BlobCheckpointStore:
    def __init__(self, container: ContainerClient) -> None:
        self._container = container

    def _blob(self, run_id: str, key: str) -> str:
        return _prefix(run_id, "checkpoints") + check_checkpoint_key(key) + _CHECKPOINT_SUFFIX

    async def save(self, run_id: str, key: str, data: bytes) -> None:
        await self._container.upload_blob(self._blob(run_id, key), data, overwrite=True)

    async def load(self, run_id: str, key: str) -> bytes | None:
        return await _download(self._container, self._blob(run_id, key))

    async def keys(self, run_id: str) -> list[str]:
        names = await _names(self._container, _prefix(run_id, "checkpoints"))
        return [n.removesuffix(_CHECKPOINT_SUFFIX) for n in names if n.endswith(_CHECKPOINT_SUFFIX)]


async def _read(container: ContainerProxy, item: str, partition: str) -> Document | None:
    try:
        return cast(Document, await container.read_item(item, partition_key=partition))
    except ResourceNotFoundError:  # CosmosResourceNotFoundError subclasses it
        return None


class CosmosRunStateStore:
    def __init__(self, container: ContainerProxy) -> None:
        self._container = container

    async def save(self, state: RunState) -> None:
        run_id = check_run_id(state.run_id)
        body: Document = {"id": run_id, "run_id": run_id, "state": state.model_dump(mode="json")}
        await self._container.upsert_item(body)

    async def load(self, run_id: str) -> RunState | None:
        doc = await _read(self._container, check_run_id(run_id), run_id)
        return RunState.model_validate(doc["state"]) if doc is not None else None


class CosmosJobStore:
    def __init__(self, container: ContainerProxy) -> None:
        self._container = container

    async def create(self, job: Job) -> None:
        body: Document = {
            "id": check_run_id(job.run_id),
            "key_id": check_key_id(job.key_id),
            "created_ts": job.created_at.timestamp(),
            "job": job.model_dump(mode="json"),
        }
        await self._container.upsert_item(body)

    async def get(self, run_id: str) -> Job | None:
        query = "SELECT * FROM c WHERE c.id = @id"
        params: list[dict[str, object]] = [{"name": "@id", "value": check_run_id(run_id)}]
        docs = [d async for d in self._container.query_items(query, parameters=params)]
        return Job.model_validate(docs[0]["job"]) if docs else None

    async def by_key(self, key_id: str, since: datetime) -> list[Job]:
        query = "SELECT * FROM c WHERE c.key_id = @key AND c.created_ts >= @since"
        params: list[dict[str, object]] = [
            {"name": "@key", "value": check_key_id(key_id)},
            {"name": "@since", "value": since.timestamp()},
        ]
        items = self._container.query_items(query, parameters=params, partition_key=key_id)
        jobs = [Job.model_validate(d["job"]) async for d in items]
        return sorted(jobs, key=lambda j: j.created_at)


class CosmosCacheStore:
    """Expiry is checked against the injected clock on read (as the local cache does); the
    item's Cosmos `ttl` only cleans up storage."""

    def __init__(self, container: ContainerProxy, clock: Clock = time.time) -> None:
        self._container = container
        self._clock = clock

    @staticmethod
    def _id(namespace: str, key: str) -> str:
        return hashlib.sha256(f"{namespace}\0{key}".encode()).hexdigest()

    async def get(self, namespace: str, key: str) -> bytes | None:
        namespace, key = check_cache_key(namespace, key)
        doc = await _read(self._container, self._id(namespace, key), namespace)
        if doc is None:
            return None
        expires = doc.get("expires_at")
        if isinstance(expires, int | float) and expires <= self._clock():
            return None
        return base64.b64decode(str(doc["value"]))

    async def put(self, namespace: str, key: str, value: bytes, ttl_s: int | None = None) -> None:
        namespace, key = check_cache_key(namespace, key)
        body: Document = {
            "id": self._id(namespace, key),
            "namespace": namespace,
            "value": base64.b64encode(value).decode(),
            "expires_at": self._clock() + ttl_s if ttl_s is not None else None,
            "ttl": ttl_s if ttl_s is not None else -1,
        }
        await self._container.upsert_item(body)


class StorageQueueJobQueue:
    def __init__(self, queue: QueueClient) -> None:
        self._queue = queue

    async def send(self, run_id: str) -> None:
        await self._queue.send_message(check_run_id(run_id))

    async def receive(self, visibility_s: int) -> QueueMessage | None:
        # the service takes 1 s … 7 days and rejects 0 ("outside the permissible range")
        visibility = min(max(visibility_s, 1), 7 * 24 * 3600)
        message = await self._queue.receive_message(visibility_timeout=visibility)
        if message is None:
            return None
        return QueueMessage(
            message_id=str(message.id),
            run_id=check_run_id(str(message.content)),
            receipt=str(message.pop_receipt),
            dequeue_count=int(message.dequeue_count or 0),
        )

    async def delete(self, message: QueueMessage) -> None:
        try:
            await self._queue.delete_message(message.message_id, message.receipt)
        except ResourceNotFoundError:
            return  # gone, or a stale receipt: the service answers both with 404 MessageNotFound


def open_azure_storage(settings: Settings, *, clock: Clock = time.time) -> Storage:
    """All stores against the account and Cosmos endpoint from the settings. Raises ConfigError
    when either is missing. Clients connect lazily; call `Storage.aclose()` when done."""
    account, endpoint = settings.azure_storage_account, settings.azure_cosmos_endpoint
    if not account:
        raise ConfigError("environment", "ARCHLENS_AZURE_STORAGE_ACCOUNT", "required for azure")
    if not endpoint:
        raise ConfigError("environment", "ARCHLENS_AZURE_COSMOS_ENDPOINT", "required for azure")
    credential = DefaultAzureCredential()
    blobs = BlobServiceClient(f"https://{account}.blob.core.windows.net", credential=credential)
    container = blobs.get_container_client(BLOB_CONTAINER)
    queue = QueueClient(
        f"https://{account}.queue.core.windows.net",
        settings.azure_queue_name,
        credential=credential,
    )
    cosmos = CosmosClient(endpoint, credential=credential)
    database = cosmos.get_database_client(DATABASE)

    async def close() -> None:
        await blobs.close()
        await queue.close()
        await cosmos.close()
        await credential.close()

    return Storage(
        artifacts=BlobArtifactStore(container),
        run_state=CosmosRunStateStore(database.get_container_client(RUNS)),
        cache=CosmosCacheStore(database.get_container_client(CACHE), clock),
        checkpoints=BlobCheckpointStore(container),
        jobs=CosmosJobStore(database.get_container_client(JOBS)),
        queue=StorageQueueJobQueue(queue),
        close=close,
    )
