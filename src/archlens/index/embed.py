"""Embedding functions for the index: protocol, deterministic fake, and a caching wrapper.

The real embedder (M2.1) goes through `archlens.llm.client`. `CachedEmbedder` keys vectors by
sha256(model, text) in the run's CacheStore (namespace `embed`), so re-indexing unchanged code
makes no provider calls.
"""

import hashlib
import math
import re
from array import array
from typing import Protocol

from archlens.storage.base import CacheStore

EMBED_TTL_S = 30 * 24 * 3600
_WORD = re.compile(r"[A-Za-z][a-z]+|[A-Z]+(?![a-z])|\d+")


class Embedder(Protocol):
    @property
    def model(self) -> str: ...

    @property
    def dim(self) -> int: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


def tokens(text: str) -> list[str]:
    """Lower-cased words, with identifiers split on `_` and camelCase."""
    return [t.lower() for t in _WORD.findall(text)]


class FakeEmbedder:
    """Deterministic hashed bag-of-words vectors (L2-normalized). Offline and free; lexical
    similarity only, which is enough for tests and for developing the index."""

    def __init__(self, dim: int = 256) -> None:
        self._dim = dim
        self.calls = 0

    @property
    def model(self) -> str:
        return f"fake-hash-{self._dim}"

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [self._vector(t) for t in texts]

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self._dim
        for token in tokens(text):
            digest = hashlib.sha1(token.encode()).digest()
            bucket = int.from_bytes(digest[:4], "little") % self._dim
            vector[bucket] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]


def pack(vector: list[float]) -> bytes:
    return array("f", vector).tobytes()


def unpack(data: bytes) -> list[float]:
    values = array("f")
    values.frombytes(data)
    return values.tolist()


class CachedEmbedder:
    """Wraps an Embedder with the content-addressed cache; counts provider calls."""

    def __init__(self, inner: Embedder, cache: CacheStore, batch_size: int = 64) -> None:
        self._inner = inner
        self._cache = cache
        self._batch = batch_size
        self.provider_calls = 0
        self.cache_hits = 0

    @property
    def model(self) -> str:
        return self._inner.model

    @property
    def dim(self) -> int:
        return self._inner.dim

    def key(self, text: str) -> str:
        return hashlib.sha256(f"{self.model}\0{text}".encode("utf-8", "replace")).hexdigest()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float] | None] = []
        for text in texts:
            cached = await self._cache.get("embed", self.key(text))
            results.append(unpack(cached) if cached is not None else None)
        self.cache_hits += sum(r is not None for r in results)
        missing = [i for i, r in enumerate(results) if r is None]
        for offset in range(0, len(missing), self._batch):
            batch = missing[offset : offset + self._batch]
            vectors = await self._inner.embed([texts[i] for i in batch])
            self.provider_calls += 1
            for i, vector in zip(batch, vectors, strict=True):
                results[i] = vector
                await self._cache.put("embed", self.key(texts[i]), pack(vector), ttl_s=EMBED_TTL_S)
        return [r for r in results if r is not None]
