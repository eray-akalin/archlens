"""Report narrative with guards (docs/LLM.md §7, CLAUDE.md rule 3).

The synthesizer sees the scores and the verified findings (claims and short snippets wrapped as
untrusted data) and returns a `Narrative`. Guards, checked deterministically:
- every finding id it cites — in `cited_findings` or in the text — must exist among the scored
  findings;
- every number in its text must be in a whitelist built from the report (scores, coverage
  percentages, verdict counts, finding and metric counts, and the 0-10 scale).
A violation gets one retry with the problems listed; after that unknown cited ids are dropped,
and unknown ids or unlisted numbers left in the text discard the narrative (`None` — the report
renders without prose). Model or budget failures also give `None`; a cassette miss propagates.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from archlens.errors import BudgetExceeded, CassetteMiss, InvalidModelOutput, LLMError
from archlens.llm.client import LLMClientProtocol
from archlens.llm.prompts import DEFAULT_PROMPTS_DIR, Prompt, joined_version, load_prompt
from archlens.llm.types import ChatMessage, LLMRequest
from archlens.llm.untrusted import wrap
from archlens.models import AssessmentReport, CodeEvidence, Narrative, Rubric

STAGE = "report"
SNIPPET_LINES = 3
FINDING_ID = re.compile(r"\b[A-Z]+-\d{2}@[0-9a-f]{12}\b")
CHECK_ID = re.compile(r"\b[A-Z]+-\d{2}\b")
NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?!\w)")
RETRY = (
    "Your narrative broke the rules: {problems}. Write it again using only finding ids and "
    "numbers that appear in the data."
)


@dataclass(frozen=True)
class SynthPrompts:
    system: Prompt
    input: Prompt

    @classmethod
    def load(cls, directory: Path = DEFAULT_PROMPTS_DIR) -> "SynthPrompts":
        return cls(
            load_prompt(directory / "synth.narrative.md"), load_prompt(directory / "synth.input.md")
        )

    @property
    def version(self) -> str:
        return joined_version(self.system, self.input)


@dataclass(frozen=True)
class Problems:
    unknown_cited: list[str] = field(default_factory=list[str])
    unknown_in_text: list[str] = field(default_factory=list[str])
    unlisted_numbers: list[str] = field(default_factory=list[str])

    def __bool__(self) -> bool:
        return bool(self.unknown_cited or self.unknown_in_text or self.unlisted_numbers)

    def describe(self) -> str:
        parts: list[str] = []
        if self.unknown_cited or self.unknown_in_text:
            ids = sorted(set(self.unknown_cited + self.unknown_in_text))
            parts.append(f"unknown finding ids {', '.join(ids)}")
        if self.unlisted_numbers:
            parts.append(f"numbers not in the data {', '.join(self.unlisted_numbers)}")
        return "; ".join(parts)


@dataclass(frozen=True)
class SynthResult:
    narrative: Narrative | None
    notes: list[str]  # what the guards or failures did, for logs and the run record


def number_whitelist(report: AssessmentReport) -> set[Decimal]:
    """Numbers the narrative may use: everything the synthesizer input shows."""
    values: set[Decimal] = {Decimal(0), Decimal(10)}
    values.add(Decimal(len(report.findings)))
    values.add(Decimal(len(report.other_findings)))
    values.add(Decimal(len(report.metric_scores)))
    values.add(Decimal(sum(m.status == "scored" for m in report.metric_scores)))
    if report.overall_score is not None:
        values.add(Decimal(str(report.overall_score)))
    for metric in report.metric_scores:
        if metric.score is not None:
            values.add(Decimal(str(metric.score)))
        values.add(Decimal(coverage_percent(metric.coverage)))
        values.update(Decimal(n) for n in metric.counts.values())
    return values


def numbers_in(text: str) -> list[str]:
    """Numbers written in `text`, ignoring the digits inside finding and check ids."""
    bare = CHECK_ID.sub(" ", FINDING_ID.sub(" ", text))
    return NUMBER.findall(bare)


def check_narrative(narrative: Narrative, report: AssessmentReport) -> Problems:
    known = {f.id for f in report.findings}
    texts = [narrative.executive_summary, *(m.text for m in narrative.per_metric)]
    allowed = number_whitelist(report)
    unlisted: list[str] = []
    for text in texts:
        for raw in numbers_in(text):
            try:
                value = Decimal(raw)
            except InvalidOperation:
                continue
            if value not in allowed and raw not in unlisted:
                unlisted.append(raw)
    return Problems(
        unknown_cited=[i for i in narrative.cited_findings if i not in known],
        unknown_in_text=sorted({i for t in texts for i in FINDING_ID.findall(t) if i not in known}),
        unlisted_numbers=unlisted,
    )


def coverage_percent(coverage: float) -> int:
    return int((Decimal(str(coverage)) * 100).quantize(Decimal(1)))


async def synthesize(
    llm: LLMClientProtocol,
    prompts: SynthPrompts,
    report: AssessmentReport,
    *,
    boundary: str,
    rubrics: Mapping[str, Rubric] | None = None,
) -> SynthResult:
    """Narrative for `report` (which must not have one yet), or None with notes. Never raises
    for model-side problems."""
    messages: list[ChatMessage] = [
        {"role": "system", "content": prompts.system.render()},
        {"role": "user", "content": render_input(prompts.input, report, boundary, rubrics or {})},
    ]
    notes: list[str] = []
    narrative: Narrative | None = None
    problems = Problems()
    for attempt in range(2):
        request = LLMRequest(
            role="synth",
            stage=STAGE,
            prompt_version=prompts.version,
            messages=list(messages),
            response_format=Narrative,
        )
        try:
            result = await llm.complete(request)
        except CassetteMiss:
            raise
        except BudgetExceeded as exc:
            return SynthResult(None, [*notes, f"skipped: budget ({exc})"])
        except InvalidModelOutput as exc:
            notes.append(f"invalid output: {exc}"[:300])
            if attempt == 0:
                messages.append({"role": "user", "content": "Answer with the requested structure."})
            continue
        except LLMError as exc:
            return SynthResult(None, [*notes, f"{type(exc).__name__}: {exc}"[:300]])
        narrative = result.parsed
        if narrative is None:
            notes.append("the synthesizer asked for tools")
            continue
        problems = check_narrative(narrative, report)
        if not problems:
            return SynthResult(_tidy(narrative, report), notes)
        notes.append(f"attempt {attempt + 1}: {problems.describe()}"[:300])
        if attempt == 0:
            messages.append({"role": "assistant", "content": narrative.model_dump_json()})
            messages.append({"role": "user", "content": RETRY.format(problems=problems.describe())})
    if narrative is None:
        return SynthResult(None, notes)
    if problems.unlisted_numbers or problems.unknown_in_text:
        return SynthResult(None, [*notes, "narrative discarded"])
    return SynthResult(_tidy(narrative, report), [*notes, "unknown cited ids dropped"])


def render_input(
    prompt: Prompt, report: AssessmentReport, boundary: str, rubrics: Mapping[str, Rubric]
) -> str:
    titles = {(r.metric, c.id): c.title for r in rubrics.values() for c in r.checks}
    findings: list[dict[str, str]] = []
    for f in report.findings:
        code = next((e for e in f.result.evidence if isinstance(e, CodeEvidence)), None)
        snippet = ""
        if code is not None:
            rows = code.snippet.split("\n")[:SNIPPET_LINES]
            body = "\n".join(f"{code.start_line + i:>4}│ {row}" for i, row in enumerate(rows))
            snippet = wrap(body, code.path, boundary)
        findings.append(
            {
                "id": f.id,
                "metric": f.result.metric,
                "verdict": f.result.verdict,
                "title": titles.get((f.result.metric, f.result.check_id), ""),
                "claim": wrap(f.result.claim, f"claim {f.id}", boundary),
                "code": snippet,
            }
        )
    metrics: list[dict[str, object]] = [
        {
            "metric": m.metric,
            "status": m.status,
            "score": m.score if m.score is not None else "none",
            "coverage": coverage_percent(m.coverage),
            "counts": m.counts,
            "capped_by": m.capped_by,
        }
        for m in report.metric_scores
    ]
    return prompt.render(
        repo=report.repo_url or "the repository",
        commit=report.commit_sha[:12],
        overall=report.overall_score if report.overall_score is not None else "not computed",
        metrics=metrics,
        findings=findings,
        other=len(report.other_findings),
    )


def _tidy(narrative: Narrative, report: AssessmentReport) -> Narrative:
    """Drop unknown cited ids (deduplicated, in order) and paragraphs for unknown metrics."""
    known = {f.id for f in report.findings}
    metrics = {m.metric for m in report.metric_scores}
    return Narrative(
        executive_summary=narrative.executive_summary,
        per_metric=[m for m in narrative.per_metric if m.metric in metrics],
        cited_findings=list(dict.fromkeys(i for i in narrative.cited_findings if i in known)),
    )
