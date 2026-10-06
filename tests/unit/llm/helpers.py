"""Shared helpers for LLM-layer tests: repo config, scripted provider, client factory."""

from pathlib import Path

from archlens.config import AppConfig, Settings, load_config
from archlens.errors import ProviderError
from archlens.llm.budget import BudgetGuard
from archlens.llm.cache import ExactCache
from archlens.llm.client import LLMClient
from archlens.llm.ratelimit import RateLimiter
from archlens.llm.types import ChatPayload, ProviderResponse, Usage
from archlens.storage.base import CacheStore

REPO = Path(__file__).parents[3]


def repo_config(**settings: object) -> AppConfig:
    """config/*.yaml from the repo; Settings from the arguments only (never the developer's .env)."""
    base = Settings(_env_file=None, config_dir=REPO / "config", **settings)  # pyright: ignore[reportCallIssue]
    return load_config(base)


class ScriptedProvider:
    def __init__(self, *items: ProviderResponse | ProviderError) -> None:
        self.items = list(items)
        self.payloads: list[ChatPayload] = []
        self.embed_calls: list[list[str]] = []

    async def chat(self, payload: ChatPayload) -> ProviderResponse:
        self.payloads.append(payload)
        item = self.items.pop(0)
        if isinstance(item, ProviderError):
            raise item
        return item

    async def aclose(self) -> None:
        pass

    async def embed(self, deployment: str, texts: list[str]) -> tuple[list[list[float]], int]:
        self.embed_calls.append(texts)
        return [[float(len(t)), 1.0] for t in texts], sum(len(t) for t in texts)


def answer(
    content: str,
    *,
    input_tokens: int = 1000,
    cached: int = 0,
    output: int = 200,
    reasoning: int = 0,
) -> ProviderResponse:
    usage = Usage(
        input_tokens=input_tokens,
        cached_input_tokens=cached,
        output_tokens=output,
        reasoning_tokens=reasoning,
    )
    return ProviderResponse(content=content, finish_reason="stop", usage=usage)


def make_client(
    provider: ScriptedProvider,
    *,
    cache_store: CacheStore | None = None,
    boundary: str | None = None,
    budget_usd: float = 1.0,
    sleeps: list[float] | None = None,
) -> LLMClient:
    config = repo_config()
    recorded = sleeps if sleeps is not None else []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    return LLMClient(
        provider,
        config.models,
        config.pricing,
        budget=BudgetGuard(budget_usd),
        limiter=RateLimiter(4, 1_000_000),
        cache=ExactCache(cache_store) if cache_store is not None else None,
        boundary=boundary,
        sleep=fake_sleep,
        jitter=lambda: 0.5,
    )
