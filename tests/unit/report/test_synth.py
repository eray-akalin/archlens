"""Synthesizer guards (docs/LLM.md §7): unknown finding ids, unlisted numbers, failures."""

from decimal import Decimal
from pathlib import Path

import pytest

from archlens.errors import BudgetExceeded, CassetteMiss, InvalidModelOutput, ProviderError
from archlens.llm.fake import FakeLLM, Scripted
from archlens.llm.untrusted import new_boundary
from archlens.models import MetricNarrative, Narrative
from archlens.report import SynthPrompts, synthesize
from archlens.report.synth import check_narrative, number_whitelist, numbers_in
from tests.unit.report.helpers import HOSTILE_CLAIM, NARRATIVE, RUBRICS, SHA, sample_report

PROMPTS = SynthPrompts.load(Path(__file__).parents[3] / "prompts")
KEY = ("synth", "synth.narrative")
SEC05 = f"SEC-05@{SHA[:12]}"
BOUNDARY = new_boundary()


def narrative(summary: str, *cited: str, metrics: tuple[str, ...] = ("security",)) -> Narrative:
    return Narrative(
        executive_summary=summary,
        per_metric=[MetricNarrative(metric=m, text=f"{m} paragraph.") for m in metrics],
        cited_findings=list(cited),
    )


async def run(*answers: Scripted) -> tuple[Narrative | None, list[str], FakeLLM]:
    llm = FakeLLM({KEY: list(answers)})
    result = await synthesize(llm, PROMPTS, sample_report(), boundary=BOUNDARY, rubrics=RUBRICS)
    return result.narrative, result.notes, llm


# --- the guards themselves (pure) --------------------------------------------------------------


def test_whitelist_holds_the_numbers_the_synthesizer_sees() -> None:
    allowed = number_whitelist(sample_report())
    for value in ("4.0", "10", "0", "85", "60", "4", "2", "1"):  # scores, coverage %, counts
        assert Decimal(value) in allowed, value
    assert Decimal("4.5") not in allowed and Decimal("2026") not in allowed


def test_numbers_inside_ids_are_ignored() -> None:
    text = f"{SEC05} and TEST-01 matter; score 4.0/10, 85% coverage, 3 issues in 2026."
    assert numbers_in(text) == ["4.0", "10", "85", "3", "2026"]


def test_check_narrative_reports_every_problem() -> None:
    report = sample_report()
    assert not check_narrative(NARRATIVE, report)
    bad = narrative(f"Security is 4.50 (was 3.2); see SEC-99@{'f' * 12}.", SEC05, "SEC-98@x")
    problems = check_narrative(bad, report)
    assert problems.unknown_cited == ["SEC-98@x"]
    assert problems.unknown_in_text == [f"SEC-99@{'f' * 12}"]
    assert problems.unlisted_numbers == ["4.50", "3.2"]
    assert (
        check_narrative(narrative("Security is 4.00 and testing 10."), report).unlisted_numbers
        == []
    )


# --- synthesize: retry, drop, discard ----------------------------------------------------------


async def test_valid_narrative_is_kept_and_tidied() -> None:
    answer = narrative(
        f"Security scores 4.0; see {SEC05}.", SEC05, SEC05, metrics=("security", "nope")
    )
    result, notes, llm = await run(answer)
    assert result is not None and notes == []
    assert result.cited_findings == [SEC05]  # deduplicated
    assert [m.metric for m in result.per_metric] == ["security"]  # unknown metric dropped
    assert len(llm.calls) == 1


async def test_unknown_cited_id_is_retried_then_fixed() -> None:
    result, notes, llm = await run(narrative("ok", "SEC-99@ffffffffffff"), narrative("ok", SEC05))
    assert result is not None and result.cited_findings == [SEC05]
    retry = llm.calls[1].messages
    assert retry[-2]["role"] == "assistant" and "unknown finding ids SEC-99@ffffffffffff" in str(
        retry[-1]["content"]
    )
    assert notes == ["attempt 1: unknown finding ids SEC-99@ffffffffffff"]


async def test_unknown_cited_ids_are_dropped_after_the_retry() -> None:
    bad = narrative("Security needs work.", SEC05, "SEC-99@ffffffffffff")
    result, notes, _ = await run(bad, bad)
    assert result is not None and result.cited_findings == [SEC05]
    assert notes[-1] == "unknown cited ids dropped"


async def test_unknown_id_in_the_text_discards_the_narrative() -> None:
    bad = narrative("See SEC-99@ffffffffffff for details.")
    result, notes, _ = await run(bad, bad)
    assert result is None and notes[-1] == "narrative discarded"


async def test_unlisted_number_is_retried_then_fixed() -> None:
    result, _, llm = await run(narrative("Security scores 4.5."), narrative("Security scores 4.0."))
    assert result is not None and result.executive_summary == "Security scores 4.0."
    assert "numbers not in the data 4.5" in str(llm.calls[1].messages[-1]["content"])


async def test_unlisted_number_twice_discards_the_narrative() -> None:
    bad = narrative("Security improved by 12% since 2025.")
    result, notes, _ = await run(bad, bad)
    assert result is None
    assert "numbers not in the data 12, 2025" in notes[0]


@pytest.mark.parametrize(
    "failure",
    [BudgetExceeded("budget reached"), ProviderError("HTTP 500", retryable=True, status=500)],
)
async def test_model_failures_give_no_narrative(failure: Exception) -> None:
    result, notes, _ = await run(failure)
    assert result is None and notes


async def test_invalid_output_is_retried() -> None:
    result, notes, _ = await run(InvalidModelOutput("truncated"), NARRATIVE)
    assert result == NARRATIVE and notes[0].startswith("invalid output")


async def test_cassette_miss_propagates() -> None:
    with pytest.raises(CassetteMiss):
        await run(CassetteMiss("no recording"))


async def test_input_wraps_repository_text_and_lists_the_numbers() -> None:
    _, _, llm = await run(NARRATIVE)
    system, user = (str(m["content"]) for m in llm.calls[0].messages)
    assert BOUNDARY not in system
    assert f'<repo_data boundary="{BOUNDARY}" source="claim {SEC05}">\n{HOSTILE_CLAIM}' in user
    assert "security: status scored, score 4.0, coverage 85%" in user
    assert "Database queries are parameterized" in user  # titles from the rubrics
    assert "2 other findings were not verified" in user
    assert llm.calls[0].role == "synth" and llm.calls[0].response_format is Narrative
