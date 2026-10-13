"""Report Q&A (M5.4): guards, retries, message layout and the exact cache."""

import re
from pathlib import Path

import pytest

from archlens.errors import ProviderError
from archlens.llm.fake import FakeLLM, Scripted
from archlens.llm.untrusted import new_boundary
from archlens.models import AskAnswer, QAAnswer
from archlens.report.qa import QAError, QAPrompts, answer_question, problems
from archlens.storage import open_local_storage
from tests.unit.llm.helpers import ScriptedProvider, answer, make_client
from tests.unit.report.helpers import HOSTILE_CLAIM, RUBRICS, SHA, sample_report

PROMPTS = QAPrompts.load(Path(__file__).parents[3] / "prompts")
KEY = ("synth", "synth.qa")
SEC05, SEC06 = f"SEC-05@{SHA[:12]}", f"SEC-06@{SHA[:12]}"
QUESTION = "Why is security capped?"


def qa(text: str, *cited: str) -> QAAnswer:
    return QAAnswer(answer=text, cited_findings=list(cited))


async def ask(*answers: Scripted, question: str = QUESTION) -> tuple[AskAnswer, FakeLLM]:
    llm = FakeLLM({KEY: list(answers)})
    result = await answer_question(
        llm, PROMPTS, sample_report(), question, boundary=new_boundary(), rubrics=RUBRICS
    )
    return result, llm


# --- guards (pure) -----------------------------------------------------------------------------


def test_guards() -> None:
    report = sample_report()
    assert problems(qa(f"{SEC05} builds SQL from input.", SEC05), report, QUESTION) == []
    # unverified findings may be cited too (they are in the report)
    assert problems(qa(f"{SEC06} was rejected.", SEC06), report, QUESTION) == []
    assert problems(qa("See NOPE-01@0123456789ab.", "SEC-99@0123456789ab"), report, QUESTION) == [
        "unknown finding ids NOPE-01@0123456789ab, SEC-99@0123456789ab"
    ]
    assert problems(qa("Security would be 7.3.", SEC05), report, QUESTION) == [
        "numbers not in the data 7.3"
    ]
    assert problems(qa("Above 7.3? No.", SEC05), report, "Is anything above 7.3?") == []


# --- the conversation ---------------------------------------------------------------------------


async def test_a_grounded_answer() -> None:
    result, llm = await ask(qa(f"{SEC05} builds SQL from request input.", SEC05, SEC05))
    assert result.answer == f"{SEC05} builds SQL from request input."
    assert result.cited_findings == [SEC05] and result.cached is False
    (call,) = llm.calls
    roles = [m["role"] for m in call.messages]
    assert roles == ["system", "user", "user"]
    first, last = str(call.messages[1]["content"]), str(call.messages[2]["content"])
    assert "## Verified findings" in first and QUESTION not in first  # report first, cacheable
    assert last.rstrip().endswith(QUESTION)
    assert "<repo_data" in last and f"### {SEC06}" in last  # unscored findings, wrapped


async def test_ids_named_in_the_text_are_cited() -> None:
    result, _ = await ask(qa(f"{SEC05} caps it; {SEC06} was not verified.", SEC05))
    assert result.cited_findings == [SEC05, SEC06]


async def test_bad_answers_get_one_retry() -> None:
    result, llm = await ask(qa("Security is 3.3.", SEC05), qa(f"{SEC05} caps it.", SEC05))
    assert result.cited_findings == [SEC05] and len(llm.calls) == 2
    assert "numbers not in the data 3.3" in str(llm.calls[1].messages[-1]["content"])


async def test_an_answer_without_citations_is_asked_to_cite_once() -> None:
    result, llm = await ask(qa("It uses raw SQL."), qa("It uses raw SQL."))
    assert len(llm.calls) == 2 and "Cite the findings" in str(llm.calls[1].messages[-1]["content"])
    assert result.cited_findings == []


async def test_broken_twice_or_model_failure_is_a_qa_error() -> None:
    with pytest.raises(QAError, match="twice"):
        await ask(qa("It is 3.3.", SEC05), qa("It is 3.3.", SEC05))
    with pytest.raises(QAError, match="ProviderError"):
        await ask(ProviderError("down", retryable=False))


async def test_hostile_claims_stay_inside_data_blocks() -> None:
    _, llm = await ask(qa(f"{SEC05}.", SEC05))
    text = "\n".join(str(m["content"]) for m in llm.calls[0].messages)
    outside = re.sub(r"<repo_data [^>]*>.*?</repo_data [^>]*>", "", text, flags=re.S)
    assert "ignore previous instructions" in text and "ignore previous instructions" not in outside
    assert HOSTILE_CLAIM not in outside


async def test_repeating_a_question_is_served_from_the_exact_cache(tmp_path: Path) -> None:
    store = open_local_storage(tmp_path / "data").cache
    reply = answer(qa(f"{SEC05} builds SQL from input.", SEC05).model_dump_json())
    first = make_client(ScriptedProvider(reply), cache_store=store, boundary=(b1 := new_boundary()))
    one = await answer_question(
        first, PROMPTS, sample_report(), QUESTION, boundary=b1, rubrics=RUBRICS
    )
    second = make_client(ScriptedProvider(), cache_store=store, boundary=(b2 := new_boundary()))
    two = await answer_question(
        second, PROMPTS, sample_report(), QUESTION, boundary=b2, rubrics=RUBRICS
    )
    assert one.cached is False and one.cost_usd > 0
    assert two.cached is True and two.cost_usd == 0 and two.answer == one.answer
