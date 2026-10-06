"""OpenAIProvider against a mocked HTTP transport: what goes over the wire, and how answers and
errors come back. No network."""

import json
from typing import Any

import httpx2
import pytest
from pydantic import BaseModel, SecretStr

from archlens.errors import ConfigError, ProviderError
from archlens.llm.client import OpenAIProvider
from archlens.llm.types import ChatPayload, ToolSpec
from archlens.models import LLMCheckOutput

BASE = "https://unit-test.invalid/openai/v1/"
CONTENT = LLMCheckOutput(
    check_id="X-01", verdict="fail", claim="c", citations=[], confidence="low"
).model_dump_json()


class Args(BaseModel):
    path: str


def completion(message: dict[str, Any], finish: str = "stop") -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-5-mini",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish,
                "message": {"role": "assistant", "refusal": None} | message,
            }
        ],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 80,
            "total_tokens": 200,
            "prompt_tokens_details": {"cached_tokens": 64},
            "completion_tokens_details": {"reasoning_tokens": 30},
        },
    }


def provider(
    responses: list[httpx2.Response], seen: list[httpx2.Request], **kwargs: Any
) -> OpenAIProvider:
    def handler(req: httpx2.Request) -> httpx2.Response:
        seen.append(req)
        return responses.pop(0)

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    options: dict[str, Any] = {
        "base_url": BASE,
        "auth": "key",
        "api_key": SecretStr("sk-test"),
    } | kwargs
    return OpenAIProvider(http_client=client, **options)


def payload(**overrides: Any) -> ChatPayload:
    fields: dict[str, Any] = {
        "deployment": "gpt-5-mini",
        "messages": [{"role": "user", "content": "hi"}],
        "response_format": LLMCheckOutput,
        "tools": (),
        "params": {"reasoning_effort": "low", "max_completion_tokens": 6000},
        "key": "k",
    } | overrides
    return ChatPayload(**fields)


async def test_request_uses_strict_structured_output_and_role_params() -> None:
    seen: list[httpx2.Request] = []
    p = provider([httpx2.Response(200, json=completion({"content": CONTENT}))], seen)
    response = await p.chat(payload())
    body = json.loads(seen[0].content)
    assert seen[0].url == BASE + "chat/completions"
    assert seen[0].headers["authorization"] == "Bearer sk-test"
    assert (
        body["model"] == "gpt-5-mini"
        and body["reasoning_effort"] == "low"
        and "temperature" not in body
    )
    assert (
        body["response_format"]["type"] == "json_schema"
        and body["response_format"]["json_schema"]["strict"] is True
    )
    assert response.content == CONTENT
    assert (response.usage.input_tokens, response.usage.cached_input_tokens) == (120, 64)
    assert (response.usage.output_tokens, response.usage.reasoning_tokens) == (80, 30)


async def test_tools_are_strict_and_tool_calls_come_back() -> None:
    seen: list[httpx2.Request] = []
    call = {
        "id": "call_9",
        "type": "function",
        "function": {"name": "read_file", "arguments": '{"path": "a.py"}'},
    }
    p = provider(
        [
            httpx2.Response(
                200, json=completion({"content": None, "tool_calls": [call]}, "tool_calls")
            )
        ],
        seen,
    )
    response = await p.chat(payload(tools=(ToolSpec("read_file", "Read a file", Args),)))
    tool = json.loads(seen[0].content)["tools"][0]
    assert (
        tool["type"] == "function"
        and tool["function"]["name"] == "read_file"
        and tool["function"]["strict"] is True
    )
    assert [(c.id, c.name, c.arguments) for c in response.tool_calls] == [
        ("call_9", "read_file", '{"path": "a.py"}')
    ]


async def test_apim_subscription_key_goes_in_its_header_not_as_bearer() -> None:
    seen: list[httpx2.Request] = []
    p = provider(
        [httpx2.Response(200, json=completion({"content": CONTENT}))],
        seen,
        key_header="Ocp-Apim-Subscription-Key",
    )
    await p.chat(payload())
    assert seen[0].headers["ocp-apim-subscription-key"] == "sk-test"
    assert "sk-test" not in seen[0].headers["authorization"]


async def test_length_finish_is_reported_not_raised() -> None:
    seen: list[httpx2.Request] = []
    p = provider(
        [httpx2.Response(200, json=completion({"content": '{"check_id": "X'}, "length"))], seen
    )
    response = await p.chat(payload())
    assert response.finish_reason == "length" and response.usage.input_tokens == 120


@pytest.mark.parametrize(
    ("status", "headers", "retryable", "retry_after"),
    [
        (429, {"retry-after": "3"}, True, 3.0),
        (429, {"retry-after-ms": "1500"}, True, 1.5),
        (503, {}, True, None),
        (400, {}, False, None),
        (401, {}, False, None),
    ],
)
async def test_http_errors_are_classified(
    status: int, headers: dict[str, str], retryable: bool, retry_after: float | None
) -> None:
    seen: list[httpx2.Request] = []
    p = provider(
        [httpx2.Response(status, headers=headers, json={"error": {"message": "nope"}})], seen
    )
    with pytest.raises(ProviderError) as info:
        await p.chat(payload())
    assert (info.value.status, info.value.retryable, info.value.retry_after) == (
        status,
        retryable,
        retry_after,
    )
    assert len(seen) == 1  # the SDK's own retries are off


async def test_connection_errors_are_retryable() -> None:
    def handler(req: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=req)

    p = OpenAIProvider(
        base_url=BASE,
        auth="key",
        api_key=SecretStr("k"),
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    with pytest.raises(ProviderError) as info:
        await p.chat(payload())
    assert info.value.retryable


async def test_embeddings() -> None:
    seen: list[httpx2.Request] = []
    body = {"object": "list", "model": "text-embedding-3-small", "data": [
        {"object": "embedding", "index": 1, "embedding": [0.0, 1.0]},
        {"object": "embedding", "index": 0, "embedding": [1.0, 0.0]},
    ], "usage": {"prompt_tokens": 7, "total_tokens": 7}}  # fmt: skip
    p = provider([httpx2.Response(200, json=body)], seen)
    vectors, tokens = await p.embed("text-embedding-3-small", ["a", "b"])
    assert vectors == [[1.0, 0.0], [0.0, 1.0]] and tokens == 7  # re-ordered by index


def test_key_auth_requires_a_key() -> None:
    with pytest.raises(ConfigError) as info:
        OpenAIProvider(base_url=BASE, auth="key", api_key=None)
    assert info.value.field == "ARCHLENS_LLM_API_KEY"
