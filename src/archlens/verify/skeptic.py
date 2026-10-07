"""Step 4 — skeptic (docs/LLM.md §6): for a verified `fail` on a critical check, a separate tool
session with its own `session_id` and ledger tries to show the finding is wrong.

`refuted=True` with citations that pass the mechanical step against the skeptic's own ledger →
`disputed`. A refutation without valid citations, or a session that couldn't finish, leaves the
finding `verified` (the step records why). Budget exhaustion is reported back so the pipeline can
skip every remaining LLM step.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from archlens.config import SessionLimits
from archlens.evidence import SnippetReader
from archlens.llm.client import LLMClientProtocol
from archlens.llm.prompts import DEFAULT_PROMPTS_DIR, Prompt, joined_version, load_prompt
from archlens.llm.session import run_tool_session
from archlens.llm.untrusted import wrap
from archlens.models import (
    CheckResult,
    CheckSpec,
    CodeEvidence,
    FileEntry,
    SkepticOutput,
    VerificationStep,
)
from archlens.tools.repo_tools import RepoTools
from archlens.verify.mechanical import check_citations

STAGE = "verify"


@dataclass(frozen=True)
class SkepticPrompts:
    system: Prompt
    finding: Prompt

    @classmethod
    def load(cls, directory: Path = DEFAULT_PROMPTS_DIR) -> "SkepticPrompts":
        return cls(
            load_prompt(directory / "skeptic.system.md"),
            load_prompt(directory / "skeptic.finding.md"),
        )

    @property
    def version(self) -> str:
        return joined_version(self.system, self.finding)


@dataclass(frozen=True)
class SkepticResult:
    step: VerificationStep
    disputed: bool
    budget_exhausted: bool


async def challenge(
    llm: LLMClientProtocol,
    prompts: SkepticPrompts,
    *,
    tools: RepoTools,
    reader: SnippetReader,
    listing: Mapping[str, FileEntry],
    limits: SessionLimits,
    check: CheckSpec,
    result: CheckResult,
    evidence: Sequence[CodeEvidence],
) -> SkepticResult:
    """Run the skeptic session for one finding. Never raises for model-side problems."""
    session = tools.session(f"{result.metric}:skeptic:{result.check_id}")
    user = prompts.finding.render(
        check=check,
        verdict=result.verdict,
        claim=wrap(result.claim, f"claim {result.check_id}", tools.boundary),
        code=session.show_evidence(evidence),
    )
    outcome = await run_tool_session(
        llm,
        role="skeptic",
        stage=STAGE,
        prompt_version=prompts.version,
        messages=[
            {
                "role": "system",
                "content": prompts.system.render(max_tool_calls=limits.max_tool_calls),
            },
            {"role": "user", "content": user},
        ],
        response_format=SkepticOutput,
        tools=session,
        limits=limits,
        metric=result.metric,
        tags=(result.check_id,),
    )
    call_id = outcome.records[-1].id if outcome.records else None
    if outcome.parsed is None:
        detail = f"skipped: {outcome.error}" + (f" ({outcome.detail})" if outcome.detail else "")
        step = VerificationStep(
            step="skeptic", passed=True, detail=detail[:300], llm_call_id=call_id
        )
        return SkepticResult(step, disputed=False, budget_exhausted=outcome.error == "budget")
    answer: SkepticOutput = outcome.parsed
    if not answer.refuted:
        step = VerificationStep(
            step="skeptic",
            passed=True,
            detail=f"not refuted: {answer.reason}"[:300],
            llm_call_id=call_id,
        )
        return SkepticResult(step, disputed=False, budget_exhausted=False)
    check_ = check_citations(
        answer.citations,
        session_id=session.session_id,
        reader=reader,
        ledger=tools.ledger,
        listing=listing,
    )
    if not check_.passed:
        detail = f"refutation ignored, citations invalid ({check_.step.detail}): {answer.reason}"
        step = VerificationStep(
            step="skeptic", passed=True, detail=detail[:300], llm_call_id=call_id
        )
        return SkepticResult(step, disputed=False, budget_exhausted=False)
    where = ", ".join(f"{e.path}:{e.start_line}-{e.end_line}" for e in check_.evidence)
    step = VerificationStep(
        step="skeptic",
        passed=False,
        detail=f"refuted with {where}: {answer.reason}"[:300],
        llm_call_id=call_id,
    )
    return SkepticResult(step, disputed=True, budget_exhausted=False)
