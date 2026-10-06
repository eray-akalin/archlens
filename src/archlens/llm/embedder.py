"""Adapts the LLM client's `embed` to the index's Embedder protocol (cost-tracked, budgeted)."""

from archlens.llm.client import LLMClientProtocol


class ClientEmbedder:
    def __init__(self, client: LLMClientProtocol, model: str, dim: int = 1536) -> None:
        self._client = client
        self._model = model
        self._dim = dim

    @property
    def model(self) -> str:
        return self._model

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = await self._client.embed(texts)
        if vectors:
            self._dim = len(vectors[0])
        return vectors
