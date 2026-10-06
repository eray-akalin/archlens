"""FakeLLM: a scripted LLMClientProtocol for tests (docs/LLM.md §10).

Script keys are `(role, prompt_id, *tags)` or `(role, prompt_id)`; prompt_id is the part of the
prompt_version before `@`. Each key holds a queue: a Pydantic instance (the parsed answer), a list
of ToolCall (the model asks for tools), or an exception to raise. An empty or missing queue is an
error, so tests fail loudly instead of silently reusing answers.
"""

from pydantic import BaseModel
from ulid import ULID

from archlens.errors import LLMError
from archlens.index.embed import FakeEmbedder
from archlens.llm.client import parse_response
from archlens.llm.types import LLMRequest, LLMResult, ProviderResponse, ToolCall
from archlens.models import LLMCallRecord

Scripted = BaseModel | list[ToolCall] | Exception


class FakeLLM:
    def __init__(self, script: dict[tuple[str, ...], list[Scripted]] | None = None) -> None:
        self._script = {key: list(items) for key, items in (script or {}).items()}
        self._records: list[LLMCallRecord] = []
        self.calls: list[LLMRequest[BaseModel]] = []
        self._embedder = FakeEmbedder()

    @property
    def records(self) -> list[LLMCallRecord]:
        return self._records

    def add(self, key: tuple[str, ...], *items: Scripted) -> None:
        self._script.setdefault(key, []).extend(items)

    async def complete[T: BaseModel](self, request: LLMRequest[T]) -> LLMResult[T]:
        prompt_id = request.prompt_version.split("@", 1)[0]
        keys = [(request.role, prompt_id, *request.tags), (request.role, prompt_id)]
        key = next((k for k in keys if self._script.get(k)), None)
        if key is None:
            raise LLMError(f"FakeLLM: nothing scripted for {keys[0]}")
        item = self._script[key].pop(0)
        self.calls.append(request)  # pyright: ignore[reportArgumentType]
        record = LLMCallRecord(
            id=str(ULID()),
            stage=request.stage,
            metric=request.metric,
            model="fake",
            prompt_version=request.prompt_version,
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
            latency_ms=0,
            cost_usd=0.0,
            cache_hit=False,
            attempt=0,
        )
        self._records.append(record)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, list):
            response = ProviderResponse(
                content=None, tool_calls=tuple(item), finish_reason="tool_calls"
            )
        else:
            if not isinstance(item, request.response_format):
                wanted = request.response_format.__name__
                raise TypeError(f"FakeLLM: scripted {type(item).__name__}, expected {wanted}")
            response = ProviderResponse(content=item.model_dump_json(), finish_reason="stop")
        return parse_response(request.response_format, response, (record,))

    async def embed(self, texts: list[str], *, stage: str = "index") -> list[list[float]]:
        return await self._embedder.embed(texts)
