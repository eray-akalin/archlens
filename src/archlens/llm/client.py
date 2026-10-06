"""The only module that talks to a model provider (CLAUDE.md rule 6, docs/LLM.md §1).

`LLMClient.complete` = exact cache → budget reservation → rate limit → provider call with retries
→ one LLMCallRecord per attempt → schema validation. Providers: `OpenAIProvider` (Azure OpenAI
v1 endpoint, APIM, or any OpenAI-compatible API) or a `CassetteProvider` (record/replay).
"""

import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import httpx2
import openai
from openai import AsyncOpenAI
from pydantic import BaseModel, JsonValue, SecretStr, ValidationError
from ulid import ULID

from archlens.config import AppConfig, ModelsConfig, Price, PricingConfig, RoleConfig
from archlens.errors import ConfigError, InvalidModelOutput, LLMError, ProviderError
from archlens.llm.budget import BudgetGuard
from archlens.llm.cache import ExactCache, request_key
from archlens.llm.cassette import CassetteProvider, Provider
from archlens.llm.cost import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    call_cost,
    estimate_messages,
    estimate_tokens,
    worst_case_cost,
)
from archlens.llm.ratelimit import RateLimiter
from archlens.llm.types import (
    ChatPayload,
    LLMRequest,
    LLMResult,
    ProviderResponse,
    ToolCall,
    Usage,
    assistant_message,
)
from archlens.models import LLMCallRecord
from archlens.storage.base import CacheStore

ENTRA_SCOPE = "https://ai.azure.com/.default"
REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")
MAX_ATTEMPTS = 5
BACKOFF_CAP_S = 30.0


class LLMClientProtocol(Protocol):
    """What pipeline stages depend on; FakeLLM implements it too."""

    @property
    def records(self) -> list[LLMCallRecord]: ...

    async def complete[T: BaseModel](self, request: LLMRequest[T]) -> LLMResult[T]: ...

    async def embed(self, texts: list[str], *, stage: str = "index") -> list[list[float]]: ...


def is_reasoning(role: RoleConfig) -> bool:
    return role.reasoning_effort is not None or role.deployment.startswith(REASONING_PREFIXES)


def role_params(role: RoleConfig) -> dict[str, JsonValue]:
    """Call parameters for a role. Reasoning models never get `temperature`."""
    params: dict[str, JsonValue] = {}
    if role.reasoning_effort is not None:
        params["reasoning_effort"] = role.reasoning_effort
    if role.temperature is not None and not is_reasoning(role):
        params["temperature"] = role.temperature
    if role.max_completion_tokens is not None:
        params["max_completion_tokens"] = role.max_completion_tokens
    return params


# --- provider ----------------------------------------------------------------------------------


def _retry_after(response: httpx2.Response) -> float | None:
    for header, scale in (("retry-after-ms", 0.001), ("retry-after", 1.0)):
        value = response.headers.get(header)
        if value is not None:
            try:
                return float(value) * scale
            except ValueError:
                return None
    return None


def _from_completion(completion: Any, finish_reason: str | None = None) -> ProviderResponse:
    choice = completion.choices[0]
    message = choice.message
    raw_calls = cast(list[Any], message.tool_calls or [])
    calls = tuple(
        ToolCall(str(c.id), str(c.function.name), str(c.function.arguments))
        for c in raw_calls
        if getattr(c, "type", "function") == "function"
    )
    usage = completion.usage
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    completion_details = getattr(usage, "completion_tokens_details", None)
    return ProviderResponse(
        content=message.content,
        tool_calls=calls,
        finish_reason=finish_reason or choice.finish_reason,
        refusal=getattr(message, "refusal", None),
        usage=Usage(
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            cached_input_tokens=int(getattr(prompt_details, "cached_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            reasoning_tokens=int(getattr(completion_details, "reasoning_tokens", 0) or 0),
        ),
    )


class OpenAIProvider:
    """Chat Completions with strict structured outputs and strict function tools (ADR-004).

    Auth `key`: API key (or, with `key_header`, an APIM subscription key in that header);
    `entra`: DefaultAzureCredential bearer tokens. SDK retries are off; LLMClient retries.
    """

    def __init__(
        self,
        *,
        base_url: str,
        auth: Literal["key", "entra"],
        api_key: SecretStr | None = None,
        key_header: str | None = None,
        http_client: httpx2.AsyncClient | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        headers: dict[str, str] = {}
        key: str | Callable[[], Awaitable[str]]
        self._credential: Any = None
        if auth == "key":
            if api_key is None:
                raise ConfigError(
                    "environment", "ARCHLENS_LLM_API_KEY", "required when ARCHLENS_LLM_AUTH=key"
                )
            if key_header:
                headers[key_header] = api_key.get_secret_value()
                key = "unused"  # never send the subscription key as a bearer token
            else:
                key = api_key.get_secret_value()
        else:
            # Sync credential (requests transport): no aiohttp dependency. The credential caches
            # tokens, so the thread hop happens about once an hour.
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider

            credential = DefaultAzureCredential()
            self._credential = credential
            fetch_token = get_bearer_token_provider(credential, ENTRA_SCOPE)

            async def entra_token() -> str:
                return await asyncio.to_thread(fetch_token)

            key = entra_token
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=key,
            default_headers=headers or None,
            max_retries=0,
            timeout=timeout_s,
            http_client=http_client,
        )

    async def chat(self, payload: ChatPayload) -> ProviderResponse:
        kwargs: dict[str, Any] = dict(payload.params)
        if payload.tools:
            kwargs["tools"] = [
                openai.pydantic_function_tool(t.args, name=t.name, description=t.description)
                for t in payload.tools
            ]
        try:
            completion = await self._client.chat.completions.parse(
                model=payload.deployment,
                messages=cast(Any, payload.messages),
                response_format=payload.response_format,
                **kwargs,
            )
        except openai.LengthFinishReasonError as exc:
            return _from_completion(exc.completion, finish_reason="length")
        except openai.ContentFilterFinishReasonError:
            return ProviderResponse(
                content=None, finish_reason="content_filter", refusal="content filter"
            )
        except ValidationError as exc:  # the SDK's own parse of a malformed answer
            raise InvalidModelOutput(f"SDK could not parse the response: {exc}"[:1000]) from exc
        except openai.APIError as exc:
            raise _provider_error(exc) from exc
        return _from_completion(completion)

    async def aclose(self) -> None:
        await self._client.close()
        if self._credential is not None:
            self._credential.close()

    async def embed(self, deployment: str, texts: list[str]) -> tuple[list[list[float]], int]:
        try:
            response = await self._client.embeddings.create(model=deployment, input=texts)
        except openai.APIError as exc:
            raise _provider_error(exc) from exc
        vectors = [list(d.embedding) for d in sorted(response.data, key=lambda d: d.index)]
        return vectors, int(response.usage.prompt_tokens)


def _provider_error(exc: openai.APIError) -> ProviderError:
    if isinstance(exc, openai.APIConnectionError):  # includes timeouts
        return ProviderError(f"connection: {exc}", retryable=True)
    if isinstance(exc, openai.APIStatusError):
        status = exc.status_code
        retryable = status == 429 or status >= 500
        return ProviderError(
            f"HTTP {status}: {exc.message}"[:500],
            retryable=retryable,
            status=status,
            retry_after=_retry_after(exc.response),
        )
    return ProviderError(str(exc)[:500], retryable=False)


# --- client ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _CallMeta:
    stage: str
    metric: str | None
    deployment: str
    prompt_version: str


class LLMClient:
    def __init__(
        self,
        provider: Provider,
        models: ModelsConfig,
        pricing: PricingConfig,
        *,
        budget: BudgetGuard,
        limiter: RateLimiter,
        cache: ExactCache | None = None,
        boundary: str | None = None,
        max_attempts: int = MAX_ATTEMPTS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._provider = provider
        self._models = models
        self._pricing = pricing
        self.budget = budget
        self._limiter = limiter
        self._cache = cache
        self.boundary = boundary
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._jitter = jitter
        self._records: list[LLMCallRecord] = []

    @classmethod
    def from_config(
        cls,
        config: AppConfig,
        *,
        cache_store: CacheStore | None,
        boundary: str | None,
        cassette_dir: Path | None = None,
    ) -> "LLMClient":
        """Client for ARCHLENS_LLM_RECORD_MODE: off (real calls), record, or replay."""
        settings = config.settings
        mode = settings.llm_record_mode
        provider: Provider
        if mode != "off" and cassette_dir is None:
            raise ConfigError(
                "environment", "ARCHLENS_LLM_RECORD_MODE", f"{mode} needs a cassette directory"
            )
        if mode == "replay":
            provider = CassetteProvider(cast(Path, cassette_dir), "replay")
        else:
            if not settings.llm_base_url:
                raise ConfigError(
                    "environment", "ARCHLENS_LLM_BASE_URL", "required for real LLM calls"
                )
            real = OpenAIProvider(
                base_url=settings.llm_base_url,
                auth=settings.llm_auth,
                api_key=settings.llm_api_key,
                key_header=settings.llm_key_header,
            )
            provider = (
                CassetteProvider(cast(Path, cassette_dir), "record", real)
                if mode == "record"
                else real
            )
        return cls(
            provider,
            config.models,
            config.pricing,
            budget=BudgetGuard(settings.run_budget_usd),
            limiter=RateLimiter(settings.max_concurrency, settings.tpm_limit),
            cache=ExactCache(cache_store) if cache_store is not None else None,
            boundary=boundary,
        )

    @property
    def records(self) -> list[LLMCallRecord]:
        return self._records

    async def aclose(self) -> None:
        """Close HTTP clients and credentials (call once per run)."""
        await self._provider.aclose()

    def _role(self, name: str) -> RoleConfig:
        role = getattr(self._models.roles, name, None)
        if not isinstance(role, RoleConfig):
            raise LLMError(f"unknown role {name!r}")
        return role

    def _price(self, deployment: str) -> Price:
        return self._pricing.deployments[
            deployment
        ]  # load_config guarantees a price per deployment

    async def complete[T: BaseModel](self, request: LLMRequest[T]) -> LLMResult[T]:
        """One structured-output call. Raises BudgetExceeded before spending, ProviderError after
        retries are exhausted, InvalidModelOutput when the answer doesn't fit the schema."""
        role = self._role(request.role)
        params = role_params(role)
        key = request_key(
            deployment=role.deployment,
            prompt_version=request.prompt_version,
            messages=request.messages,
            tools=request.tools,
            response_format=request.response_format,
            params=params,
            boundary=self.boundary,
        )
        meta = _CallMeta(request.stage, request.metric, role.deployment, request.prompt_version)
        if (
            self._cache is not None
            and request.use_cache
            and (hit := await self._cache.get(key)) is not None
        ):
            record = self._record(meta, Usage(), 0.0, 0, cache_hit=True, attempt=0)
            return self._result(request, hit, (record,))

        payload = ChatPayload(
            role.deployment, request.messages, request.response_format, request.tools, params, key
        )
        schemas = json.dumps(
            [
                request.response_format.model_json_schema(),
                *[t.args.model_json_schema() for t in request.tools],
            ]
        )
        input_estimate = estimate_messages(request.messages) + estimate_tokens(schemas)
        max_output = role.max_completion_tokens or DEFAULT_MAX_OUTPUT_TOKENS
        price = self._price(role.deployment)
        reservation = self.budget.reserve(worst_case_cost(price, input_estimate, max_output))
        attempts: list[LLMCallRecord] = []
        try:
            response = await self._with_retries(
                lambda: self._provider.chat(payload),
                lambda r: r.usage,
                meta,
                price,
                input_estimate,
                attempts,
            )
        except BaseException:
            self.budget.release(reservation)
            raise
        self.budget.settle(reservation, sum(r.cost_usd for r in attempts))
        cacheable = (
            response.finish_reason not in {"length", "content_filter"} and not response.refusal
        )
        if self._cache is not None and request.use_cache and cacheable:
            await self._cache.put(key, response)
        return self._result(request, response, tuple(attempts))

    async def embed(self, texts: list[str], *, stage: str = "index") -> list[list[float]]:
        role = self._role("embed")
        price = self._price(role.deployment)
        estimate = sum(estimate_tokens(t) for t in texts)
        reservation = self.budget.reserve(estimate * price.input / 1_000_000)
        meta = _CallMeta(stage, None, role.deployment, "embed")
        attempts: list[LLMCallRecord] = []
        try:
            vectors, _ = await self._with_retries(
                lambda: self._provider.embed(role.deployment, texts),
                lambda r: Usage(input_tokens=r[1]),
                meta,
                price,
                estimate,
                attempts,
            )
        except BaseException:
            self.budget.release(reservation)
            raise
        self.budget.settle(reservation, sum(r.cost_usd for r in attempts))
        return vectors

    async def _with_retries[R](
        self,
        call: Callable[[], Awaitable[R]],
        usage_of: Callable[[R], Usage],
        meta: _CallMeta,
        price: Price,
        tokens: int,
        attempts: list[LLMCallRecord],
    ) -> R:
        for attempt in range(self._max_attempts):
            started = time.monotonic()
            try:
                async with self._limiter.slot(tokens):
                    result = await call()
            except ProviderError as exc:
                latency = int((time.monotonic() - started) * 1000)
                attempts.append(
                    self._record(meta, Usage(), 0.0, latency, cache_hit=False, attempt=attempt)
                )
                if not exc.retryable or attempt == self._max_attempts - 1:
                    raise
                await self._sleep(self._backoff(attempt, exc.retry_after))
                continue
            usage = usage_of(result)
            latency = int((time.monotonic() - started) * 1000)
            attempts.append(
                self._record(
                    meta, usage, call_cost(price, usage), latency, cache_hit=False, attempt=attempt
                )
            )
            return result
        raise AssertionError("unreachable")

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return max(retry_after, 0.0)
        return min(BACKOFF_CAP_S, 2.0**attempt) * (0.5 + self._jitter() / 2)

    def _record(
        self,
        meta: _CallMeta,
        usage: Usage,
        cost: float,
        latency_ms: int,
        *,
        cache_hit: bool,
        attempt: int,
    ) -> LLMCallRecord:
        record = LLMCallRecord(
            id=str(ULID()),
            stage=meta.stage,
            metric=meta.metric,
            model=meta.deployment,
            prompt_version=meta.prompt_version,
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            latency_ms=latency_ms,
            cost_usd=round(cost, 8),
            cache_hit=cache_hit,
            attempt=attempt,
        )
        self._records.append(record)
        return record

    @staticmethod
    def _result[T: BaseModel](
        request: LLMRequest[T], response: ProviderResponse, records: tuple[LLMCallRecord, ...]
    ) -> LLMResult[T]:
        return parse_response(request.response_format, response, records)


def parse_response[T: BaseModel](
    response_format: type[T], response: ProviderResponse, records: tuple[LLMCallRecord, ...]
) -> LLMResult[T]:
    """Tool calls, or the answer validated against `response_format`. Raises InvalidModelOutput."""
    message = assistant_message(response)
    if response.tool_calls:
        return LLMResult(None, response.tool_calls, message, records)
    if response.refusal:
        raise InvalidModelOutput(f"refused: {response.refusal}", content=response.content)
    if response.finish_reason == "length":
        raise InvalidModelOutput(
            "output truncated at max_completion_tokens", content=response.content
        )
    if response.content is None:
        raise InvalidModelOutput("empty response")
    try:
        parsed = response_format.model_validate_json(response.content)
    except ValidationError as exc:
        raise InvalidModelOutput(str(exc)[:1000], content=response.content) from exc
    return LLMResult(parsed, (), message, records)
