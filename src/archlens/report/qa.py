"""Report Q&A (docs/LLM.md §7, M5.4): answer a question from one `AssessmentReport` only.

Messages, static first: the Q&A system prompt, the report as the narrative synthesizer sees it
(`synth.input`, the same for every question about this report — so the provider's prompt cache
covers it), then the findings that did not affect a score and the question. Claims and verifier
notes are wrapped as untrusted data; the question is the user's.

Guards, as for the narrative (CLAUDE.md rule 3): every cited finding id — in `cited_findings` or
in the text — must exist in the report, and every number in the answer must come from the report
(the narrative's whitelist plus the numbers of unscored findings' claims) or from the question. An
answer citing nothing gets asked once to cite. A violation gets one retry with the problems
listed; still broken → `QAError`. Ids named in the text join `cited_findings`. Identical
questions about an identical report are answered from the exact cache.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from archlens.errors import ArchLensError, CassetteMiss, InvalidModelOutput, LLMError
from archlens.llm.client import LLMClientProtocol
from archlens.llm.prompts import DEFAULT_PROMPTS_DIR, Prompt, joined_version, load_prompt
from archlens.llm.types import ChatMessage, LLMRequest
from archlens.llm.untrusted import wrap
from archlens.models import AskAnswer, AssessmentReport, QAAnswer, Rubric
from archlens.report.synth import FINDING_ID, number_whitelist, numbers_in, render_input

STAGE = "qa"
RETRY = (
    "Your answer broke the rules: {problems}. Answer again, citing only finding ids and using only "
    "numbers that appear in the data or the question."
)
CITE = (
    "Cite the findings your answer rests on, by their exact ids, in the text and in cited_findings."
)


class QAError(ArchLensError):
    """No grounded answer: the model failed, or its answer broke the guards twice."""


@dataclass(frozen=True)
class QAPrompts:
    system: Prompt
    report: Prompt  # synth.input
    question: Prompt  # synth.qa_input

    @classmethod
    def load(cls, directory: Path = DEFAULT_PROMPTS_DIR) -> "QAPrompts":
        return cls(
            load_prompt(directory / "synth.qa.md"),
            load_prompt(directory / "synth.input.md"),
            load_prompt(directory / "synth.qa_input.md"),
        )

    @property
    def version(self) -> str:
        return joined_version(self.system, self.report, self.question)


def problems(answer: QAAnswer, report: AssessmentReport, question: str) -> list[str]:
    """What breaks the guards: unknown finding ids (cited or in the text), numbers that are
    neither in the report nor in the question."""
    known = {f.id for f in [*report.findings, *report.other_findings]}
    unknown = sorted(
        {i for i in [*answer.cited_findings, *FINDING_ID.findall(answer.answer)] if i not in known}
    )
    allowed = number_whitelist(report)
    for finding in report.other_findings:
        allowed.update(_decimals(numbers_in(finding.result.claim)))
    allowed.update(_decimals(numbers_in(question)))
    unlisted = [raw for raw in dict.fromkeys(numbers_in(answer.answer))
                if (value := _decimal(raw)) is not None and value not in allowed]  # fmt: skip
    found: list[str] = []
    if unknown:
        found.append(f"unknown finding ids {', '.join(unknown)}")
    if unlisted:
        found.append(f"numbers not in the data {', '.join(unlisted)}")
    return found


async def answer_question(
    llm: LLMClientProtocol,
    prompts: QAPrompts,
    report: AssessmentReport,
    question: str,
    *,
    boundary: str,
    rubrics: Mapping[str, Rubric] | None = None,
) -> AskAnswer:
    """A grounded answer to `question` about `report`. Raises QAError; a cassette miss
    propagates (tests)."""
    rubrics = rubrics or {}
    titles = {(r.metric, c.id): c.title for r in rubrics.values() for c in r.checks}
    other = [
        {
            "id": f.id, "metric": f.result.metric, "verdict": f.result.verdict,
            "status": f.verification.status,
            "title": titles.get((f.result.metric, f.result.check_id), ""),
            "claim": wrap(f.result.claim, f"claim {f.id}", boundary),
            "why": wrap(why, f"note {f.id}", boundary) if (why := _why(f.result.reason,
                [s.detail for s in f.verification.steps])) else "",
        }
        for f in report.other_findings
    ]  # fmt: skip
    messages: list[ChatMessage] = [
        {"role": "system", "content": prompts.system.render()},
        {"role": "user", "content": render_input(prompts.report, report, boundary, rubrics)},
        {"role": "user", "content": prompts.question.render(other=other, question=question)},
    ]
    calls: list[tuple[float, bool]] = []  # (cost, cache hit) per model call
    for attempt in range(2):
        request = LLMRequest(
            role="synth", stage=STAGE, prompt_version=prompts.version,
            messages=list(messages), response_format=QAAnswer,
        )  # fmt: skip
        try:
            result = await llm.complete(request)
        except CassetteMiss:
            raise
        except InvalidModelOutput as exc:
            if attempt == 0:
                messages.append({"role": "user", "content": "Answer with the requested structure."})
                continue
            raise QAError(f"invalid answer: {exc}") from exc
        except LLMError as exc:  # budget included
            raise QAError(f"{type(exc).__name__}: {exc}") from exc
        calls.extend((r.cost_usd, r.cache_hit) for r in result.records)
        answer = result.parsed
        if answer is None:
            raise QAError("the model asked for tools")
        found = problems(answer, report, question)
        if not found and (answer.cited_findings or attempt == 1):
            return AskAnswer(
                run_id=report.run_id, answer=answer.answer.strip(),
                cited_findings=_cited(answer),
                cached=all(hit for _, hit in calls), cost_usd=round(sum(c for c, _ in calls), 6),
            )  # fmt: skip
        if attempt == 0:
            messages.append({"role": "assistant", "content": answer.model_dump_json()})
            follow_up = RETRY.format(problems="; ".join(found)) if found else CITE
            messages.append({"role": "user", "content": follow_up})
            continue
        raise QAError(f"the answer broke the rules twice: {'; '.join(found)}")
    raise QAError("no answer")


def _cited(answer: QAAnswer) -> list[str]:
    """`cited_findings` plus the ids written in the text (all known by now), in order, once."""
    return list(dict.fromkeys([*answer.cited_findings, *FINDING_ID.findall(answer.answer)]))


def _why(reason: str | None, details: list[str]) -> str:
    return reason or (details[-1] if details else "")


def _decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _decimals(raws: list[str]) -> set[Decimal]:
    return {value for raw in raws if (value := _decimal(raw)) is not None}
