"""Record/replay of provider responses for tests (docs/LLM.md §10).

`record` calls the real provider and writes `<dir>/<key[:24]>.json`; `replay` serves those files
and raises CassetteMiss when one is missing (a test failure, never a network call). Files hold
only the deployment, the request key and the normalized response — no headers, host or keys.
"""

import hashlib
import json
from pathlib import Path
from typing import Literal, Protocol

from archlens.errors import CassetteMiss
from archlens.llm.types import ChatPayload, ProviderResponse

CassetteMode = Literal["record", "replay"]


class Provider(Protocol):
    async def chat(self, payload: ChatPayload) -> ProviderResponse: ...

    async def embed(self, deployment: str, texts: list[str]) -> tuple[list[list[float]], int]:
        """(vectors, input tokens)."""
        ...

    async def aclose(self) -> None: ...


class CassetteProvider:
    def __init__(self, directory: Path, mode: CassetteMode, inner: Provider | None = None) -> None:
        if mode == "record" and inner is None:
            raise ValueError("record mode needs a real provider")
        self._dir = directory
        self._mode = mode
        self._inner = inner

    async def aclose(self) -> None:
        if self._inner is not None:
            await self._inner.aclose()

    def _path(self, key: str) -> Path:
        return self._dir / f"{key[:24]}.json"

    async def chat(self, payload: ChatPayload) -> ProviderResponse:
        path = self._path(payload.key)
        if self._mode == "replay":
            if not path.is_file():
                raise CassetteMiss(f"no cassette {path} for {payload.deployment}")
            return ProviderResponse.from_json(json.dumps(json.loads(path.read_text())["response"]))
        assert self._inner is not None
        response = await self._inner.chat(payload)
        self._write(
            path,
            {
                "deployment": payload.deployment,
                "key": payload.key,
                "response": json.loads(response.to_json()),
            },
        )
        return response

    async def embed(self, deployment: str, texts: list[str]) -> tuple[list[list[float]], int]:
        key = hashlib.sha256(json.dumps([deployment, texts]).encode()).hexdigest()
        path = self._path(f"embed-{key}")
        if self._mode == "replay":
            if not path.is_file():
                raise CassetteMiss(f"no cassette {path} for {deployment}")
            data = json.loads(path.read_text())
            return data["vectors"], int(data["input_tokens"])
        assert self._inner is not None
        vectors, tokens = await self._inner.embed(deployment, texts)
        self._write(
            path, {"deployment": deployment, "key": key, "vectors": vectors, "input_tokens": tokens}
        )
        return vectors, tokens

    def _write(self, path: Path, data: object) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
