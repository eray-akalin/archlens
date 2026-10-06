"""Cassettes (record/replay/miss) and FakeLLM scripting."""

import json
from pathlib import Path

import pytest

from archlens.errors import CassetteMiss, LLMError
from archlens.llm.cassette import CassetteProvider
from archlens.llm.fake import FakeLLM
from archlens.llm.types import ChatPayload, LLMRequest, ToolCall
from archlens.models import LLMCheckOutput, Narrative
from tests.unit.llm.helpers import ScriptedProvider, answer

GOOD = LLMCheckOutput(check_id="A-01", verdict="pass", claim="c", citations=[], confidence="high")


def payload(key: str = "k" * 64) -> ChatPayload:
    return ChatPayload(
        "gpt-5-mini", [{"role": "user", "content": "x"}], LLMCheckOutput, (), {}, key
    )


async def test_record_then_replay(tmp_path: Path) -> None:
    inner = ScriptedProvider(answer(GOOD.model_dump_json()))
    recorded = await CassetteProvider(tmp_path, "record", inner).chat(payload())
    (file,) = tmp_path.iterdir()
    data = json.loads(file.read_text())
    assert set(data) == {"deployment", "key", "response"}  # no headers, host or credentials
    replayed = await CassetteProvider(tmp_path, "replay").chat(payload())
    assert replayed == recorded


async def test_replay_miss_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(CassetteMiss):
        await CassetteProvider(tmp_path, "replay").chat(payload("z" * 64))


async def test_embed_cassettes(tmp_path: Path) -> None:
    inner = ScriptedProvider()
    recorded = await CassetteProvider(tmp_path, "record", inner).embed("emb", ["ab"])
    assert await CassetteProvider(tmp_path, "replay").embed("emb", ["ab"]) == recorded
    with pytest.raises(CassetteMiss):
        await CassetteProvider(tmp_path, "replay").embed("emb", ["other"])


def test_record_mode_needs_a_real_provider(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="record"):
        CassetteProvider(tmp_path, "record")


def req(
    tags: tuple[str, ...] = (), response_format: type = LLMCheckOutput
) -> LLMRequest[LLMCheckOutput]:
    return LLMRequest(
        role="evaluator",
        stage="evaluate",
        prompt_version="evaluator.metric@1.0.0+00000000",
        messages=[{"role": "user", "content": "x"}],
        response_format=response_format,
        tags=tags,
    )


async def test_fake_llm_scripts_by_key_and_tags() -> None:
    other = GOOD.model_copy(update={"check_id": "A-02"})
    fake = FakeLLM(
        {
            ("evaluator", "evaluator.metric", "A-02"): [other],
            ("evaluator", "evaluator.metric"): [GOOD],
        }
    )
    assert (await fake.complete(req(("A-02",)))).parsed == other
    assert (await fake.complete(req())).parsed == GOOD
    assert len(fake.calls) == 2 and len(fake.records) == 2 and fake.records[0].cost_usd == 0
    with pytest.raises(LLMError, match="nothing scripted"):
        await fake.complete(req())  # queue exhausted


async def test_fake_llm_tool_calls_errors_and_type_checks() -> None:
    calls = [ToolCall("c1", "read_file", '{"path": "a.py"}')]
    fake = FakeLLM(
        {
            ("evaluator", "evaluator.metric"): [
                calls,
                RuntimeError("boom"),
                Narrative(executive_summary="", per_metric=[], cited_findings=[]),
            ]
        }
    )
    result = await fake.complete(req())
    assert result.parsed is None and result.tool_calls == tuple(calls)
    with pytest.raises(RuntimeError, match="boom"):
        await fake.complete(req())
    with pytest.raises(TypeError, match="expected LLMCheckOutput"):
        await fake.complete(req())
