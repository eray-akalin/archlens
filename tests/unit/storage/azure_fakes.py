"""In-memory stand-ins for the Azure SDK calls the azure backend makes, so the storage contract
suite runs against `archlens.storage.azure` offline. They mimic the behaviors the backend relies
on: NotFound errors, blob prefix listing, Cosmos partitions and the two query shapes it issues,
queue visibility timeouts (1 s … 7 days), pop receipts (a stale one is a 404 `MessageNotFound`,
as for a deleted message — verified against the service) and dequeue counts. The `live` suite
checks the real services."""

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from ulid import ULID


@dataclass
class _Blob:
    name: str


class _Downloader:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def readall(self) -> bytes:
        return self._data


class FakeBlobContainer:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    async def upload_blob(self, name: str, data: bytes, overwrite: bool = False) -> None:
        if name in self.blobs and not overwrite:
            raise HttpResponseError(message="BlobAlreadyExists")
        self.blobs[name] = bytes(data)

    async def download_blob(self, name: str) -> _Downloader:
        if name not in self.blobs:
            raise ResourceNotFoundError(message=f"blob {name} not found")
        return _Downloader(self.blobs[name])

    async def list_blobs(self, name_starts_with: str | None = None) -> AsyncIterator[_Blob]:
        for name in sorted(self.blobs):
            if name_starts_with is None or name.startswith(name_starts_with):
                yield _Blob(name)


class FakeCosmosContainer:
    def __init__(self, partition_field: str) -> None:
        self.partition_field = partition_field
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    async def upsert_item(self, body: dict[str, Any]) -> dict[str, Any]:
        doc = json.loads(json.dumps(body))  # the service stores JSON
        if any(c in doc["id"] for c in "/\\?#") or len(doc["id"]) > 255:
            raise HttpResponseError(message="invalid id")
        self.items[(str(doc[self.partition_field]), doc["id"])] = doc
        return doc

    async def read_item(self, item: str, partition_key: str) -> dict[str, Any]:
        try:
            return self.items[(partition_key, item)]
        except KeyError:
            raise CosmosResourceNotFoundError(status_code=404, message="not found") from None

    async def query_items(
        self,
        query: str,
        parameters: list[dict[str, Any]] | None = None,
        partition_key: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        values = {p["name"]: p["value"] for p in parameters or []}
        conditions = query.split(" WHERE ", 1)[1].split(" AND ")
        for (partition, _), doc in sorted(self.items.items()):
            if partition_key is not None and partition != partition_key:
                continue
            if all(_holds(doc, condition, values) for condition in conditions):
                yield doc


def _holds(doc: dict[str, Any], condition: str, values: dict[str, Any]) -> bool:
    field, op, param = condition.split()
    actual, wanted = doc.get(field.removeprefix("c.")), values[param]
    if op == "=":
        return actual == wanted
    if op == ">=":
        return actual is not None and actual >= wanted
    raise AssertionError(f"the fake doesn't support {condition!r}")


@dataclass
class _Message:
    id: str
    content: str
    visible_at: float
    dequeue_count: int = 0
    pop_receipt: str = ""


class FakeQueue:
    def __init__(self, clock: Callable[[], float]) -> None:
        self.clock = clock
        self.messages: list[_Message] = []

    async def send_message(self, content: str) -> None:
        self.messages.append(_Message(str(ULID()), content, self.clock()))

    async def receive_message(self, visibility_timeout: int) -> _Message | None:
        if not 1 <= visibility_timeout <= 7 * 24 * 3600:
            raise HttpResponseError(message="outside the permissible range")  # as the service
        now = self.clock()
        for message in self.messages:
            if message.visible_at <= now:
                message.visible_at = now + visibility_timeout
                message.dequeue_count += 1
                message.pop_receipt = str(ULID())
                return _Message(**vars(message))
        return None

    async def delete_message(self, message: str, pop_receipt: str) -> None:
        found = next((m for m in self.messages if m.id == message), None)
        if found is None or found.pop_receipt != pop_receipt:
            raise ResourceNotFoundError(message="MessageNotFound")  # what the service answers
        self.messages.remove(found)
