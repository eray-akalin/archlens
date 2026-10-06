"""Exact LLM cache (docs/LLM.md §8) and the request key it shares with cassettes.

Key = sha256 of (deployment, prompt_version, messages, tool schemas, response schema, params)
after the run's `repo_data` boundary is replaced by a constant, so identical requests from two runs
(with different random boundaries) hit the same entry. No semantic matching, ever (ADR-006).
"""

import hashlib
import json

from pydantic import BaseModel, JsonValue

from archlens.llm.types import ChatMessage, ProviderResponse, ToolSpec
from archlens.llm.untrusted import PLACEHOLDER
from archlens.storage.base import CacheStore

NAMESPACE = "llm"
TTL_S = 30 * 24 * 3600


def normalize_boundary(value: JsonValue, boundary: str | None) -> JsonValue:
    """Deep copy of `value` with every occurrence of `boundary` replaced by the placeholder."""
    if boundary is None:
        return value
    if isinstance(value, str):
        return value.replace(boundary, PLACEHOLDER)
    if isinstance(value, list):
        return [normalize_boundary(v, boundary) for v in value]
    if isinstance(value, dict):
        return {k: normalize_boundary(v, boundary) for k, v in value.items()}
    return value


def request_key(
    *,
    deployment: str,
    prompt_version: str,
    messages: list[ChatMessage],
    tools: tuple[ToolSpec, ...],
    response_format: type[BaseModel],
    params: dict[str, JsonValue],
    boundary: str | None,
) -> str:
    payload = {
        "deployment": deployment,
        "prompt_version": prompt_version,
        "messages": normalize_boundary(list[JsonValue](messages), boundary),
        "tools": [
            {"name": t.name, "description": t.description, "schema": t.args.model_json_schema()}
            for t in tools
        ],
        "response_schema": response_format.model_json_schema(),
        "params": params,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class ExactCache:
    def __init__(self, store: CacheStore) -> None:
        self._store = store

    async def get(self, key: str) -> ProviderResponse | None:
        data = await self._store.get(NAMESPACE, key)
        return None if data is None else ProviderResponse.from_json(data.decode())

    async def put(self, key: str, response: ProviderResponse) -> None:
        await self._store.put(NAMESPACE, key, response.to_json().encode(), ttl_s=TTL_S)
