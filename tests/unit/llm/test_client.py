"""LLMClient: params per role, exact cache across runs, retries, budget, parsing, records."""

from pathlib import Path

import pytest
from pydantic import BaseModel

from archlens.errors import BudgetExceeded, InvalidModelOutput, ProviderError
from archlens.llm.client import role_params
from archlens.llm.types import LLMRequest, ProviderResponse, ToolCall, ToolSpec
from archlens.llm.untrusted import new_boundary, wrap
from archlens.models import Citation, LLMCheckOutput
from archlens.storage import open_local_storage
from tests.unit.llm.helpers import ScriptedProvider, answer, make_client, repo_config

GOOD = LLMCheckOutput(
    check_id="SEC-04",
    verdict="pass",
    claim="Bodies are validated.",
    citations=[Citation(path="a.py", start_line=1, end_line=2)],
    confidence="high",
)


class ReadFileArgs(BaseModel):
    path: str


def request(boundary: str | None = None, **overrides: object) -> LLMRequest[LLMCheckOutput]:
    content = "judge this:\n" + (
        wrap("def f(): ...", "a.py", boundary) if boundary else "def f(): ..."
    )
    fields: dict[str, object] = {
        "role": "evaluator",
        "stage": "evaluate",
        "prompt_version": "evaluator.metric@1.0.0+abcd1234",
        "messages": [{"role": "system", "content": "rules"}, {"role": "user", "content": content}],
        "response_format": LLMCheckOutput,
        "metric": "security",
    } | overrides
    return LLMRequest(**fields)  # pyright: ignore[reportArgumentType]


def test_role_params_never_send_temperature_to_reasoning_models() -> None:
    roles = repo_config().models.roles
    assert role_params(roles.evaluator) == {
        "reasoning_effort": "low",
        "max_completion_tokens": 6000,
    }
    assert role_params(roles.verifier) == {"temperature": 0, "max_completion_tokens": 400}
    sneaky = roles.verifier.model_copy(update={"deployment": "gpt-5-nano", "temperature": 0.7})
    assert "temperature" not in role_params(sneaky)  # deployment name says reasoning model


async def test_successful_call_is_parsed_and_recorded() -> None:
    provider = ScriptedProvider(
        answer(GOOD.model_dump_json(), input_tokens=1000, cached=200, output=300, reasoning=100)
    )
    client = make_client(provider)
    result = await client.complete(request())
    assert result.parsed == GOOD and result.tool_calls == ()
    (record,) = result.records
    assert (record.model, record.stage, record.metric, record.cache_hit, record.attempt) == (
        "gpt-5-mini",
        "evaluate",
        "security",
        False,
        0,
    )
    assert (
        record.input_tokens,
        record.cached_input_tokens,
        record.output_tokens,
        record.reasoning_tokens,
    ) == (1000, 200, 300, 100)
    assert record.cost_usd == pytest.approx((800 * 0.25 + 200 * 0.025 + 300 * 2.0) / 1e6)
    assert client.records == [record]
    assert (
        client.budget.spent_usd == pytest.approx(record.cost_usd)
        and client.budget.reserved_usd == 0
    )
    assert provider.payloads[0].params == {"reasoning_effort": "low", "max_completion_tokens": 6000}


async def test_exact_cache_hits_across_runs_with_different_boundaries(tmp_path: Path) -> None:
    store = open_local_storage(tmp_path).cache
    first_run = make_client(
        ScriptedProvider(answer(GOOD.model_dump_json())),
        cache_store=store,
        boundary=(b1 := new_boundary()),
    )
    await first_run.complete(request(b1))

    second_provider = ScriptedProvider()  # would raise if called
    second_run = make_client(second_provider, cache_store=store, boundary=(b2 := new_boundary()))
    result = await second_run.complete(request(b2))
    assert b1 != b2 and result.parsed == GOOD
    assert second_provider.payloads == []
    (record,) = result.records
    assert record.cache_hit and record.cost_usd == 0 and second_run.budget.spent_usd == 0


async def test_cache_key_changes_with_prompt_version(tmp_path: Path) -> None:
    store = open_local_storage(tmp_path).cache
    provider = ScriptedProvider(answer(GOOD.model_dump_json()), answer(GOOD.model_dump_json()))
    client = make_client(provider, cache_store=store)
    await client.complete(request())
    await client.complete(request(prompt_version="evaluator.metric@1.0.1+ffff0000"))
    assert len(provider.payloads) == 2


async def test_retries_honor_retry_after_and_record_each_attempt() -> None:
    provider = ScriptedProvider(
        ProviderError("HTTP 429", retryable=True, status=429, retry_after=7.0),
        ProviderError("HTTP 503", retryable=True, status=503),
        answer(GOOD.model_dump_json()),
    )
    sleeps: list[float] = []
    client = make_client(provider, sleeps=sleeps)
    result = await client.complete(request())
    assert [r.attempt for r in result.records] == [0, 1, 2]
    assert [r.cost_usd for r in result.records][:2] == [0.0, 0.0]
    assert sleeps == [7.0, pytest.approx(2.0 * 0.75)]  # retry-after, then 2^1 x jitter(0.5→0.75)


async def test_non_retryable_errors_fail_fast_and_release_the_budget() -> None:
    client = make_client(ScriptedProvider(ProviderError("HTTP 400", retryable=False, status=400)))
    with pytest.raises(ProviderError, match="400"):
        await client.complete(request())
    assert (
        len(client.records) == 1
        and client.budget.reserved_usd == 0
        and client.budget.spent_usd == 0
    )


async def test_retries_stop_after_max_attempts() -> None:
    provider = ScriptedProvider(
        *[ProviderError("HTTP 500", retryable=True, status=500) for _ in range(5)]
    )
    client = make_client(provider)
    with pytest.raises(ProviderError):
        await client.complete(request())
    assert [r.attempt for r in client.records] == [0, 1, 2, 3, 4]


async def test_budget_guard_refuses_before_any_provider_call() -> None:
    provider = ScriptedProvider()
    client = make_client(provider, budget_usd=0.001)  # evaluator worst case is ~$0.012
    with pytest.raises(BudgetExceeded):
        await client.complete(request())
    assert provider.payloads == [] and client.records == []


async def test_tool_calls_are_returned_unparsed() -> None:
    calls = (ToolCall("call_1", "read_file", '{"path": "a.py"}'),)
    provider = ScriptedProvider(
        ProviderResponse(content=None, tool_calls=calls, finish_reason="tool_calls")
    )
    client = make_client(provider)
    tools = (ToolSpec("read_file", "Read a file", ReadFileArgs),)
    result = await client.complete(request(tools=tools))
    assert result.parsed is None and result.tool_calls == calls
    assert result.message["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "read_file", "arguments": '{"path": "a.py"}'},
        }
    ]
    assert provider.payloads[0].tools == tools


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (ProviderResponse(content='{"check_id": "X"}', finish_reason="stop"), "validation error"),
        (ProviderResponse(content="not json", finish_reason="stop"), "Invalid JSON"),
        (ProviderResponse(content='{"check_id": "X"', finish_reason="length"), "truncated"),
        (ProviderResponse(content=None, refusal="I can't", finish_reason="stop"), "refused"),
        (ProviderResponse(content=None, finish_reason="stop"), "empty"),
    ],
)
async def test_unusable_answers_raise_invalid_model_output(
    response: ProviderResponse, message: str
) -> None:
    client = make_client(ScriptedProvider(response))
    with pytest.raises(InvalidModelOutput, match=message):
        await client.complete(request())
    assert len(client.records) == 1  # the spend is still recorded


async def test_truncated_answers_are_not_cached(tmp_path: Path) -> None:
    store = open_local_storage(tmp_path).cache
    provider = ScriptedProvider(
        ProviderResponse(content="{", finish_reason="length"), answer(GOOD.model_dump_json())
    )
    client = make_client(provider, cache_store=store)
    with pytest.raises(InvalidModelOutput):
        await client.complete(request())
    assert (await client.complete(request())).parsed == GOOD
    assert len(provider.payloads) == 2


async def test_embed_is_budgeted_and_recorded() -> None:
    provider = ScriptedProvider()
    client = make_client(provider)
    vectors = await client.embed(["ab", "cde"])
    assert vectors == [[2.0, 1.0], [3.0, 1.0]]
    (record,) = client.records
    assert (record.model, record.stage, record.input_tokens) == (
        "text-embedding-3-small",
        "index",
        5,
    )
    assert record.cost_usd == pytest.approx(5 * 0.02 / 1e6)
