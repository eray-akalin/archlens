"""Verification pipeline: `CheckResult`s → `Finding`s (ARCHITECTURE.md §2.5).

- deterministic → `verified` (evidence comes from facts); LLM `unknown` → `unverified`.
- cited → mechanical, then entailment: both pass → `verified`; mechanical fails → `rejected`;
  entailment `no` → `rejected`, `insufficient` or no answer → `unverified`.
- no citations with fail/partial on an `absence_allowed` check, or NA on an `na_allowed` check →
  absence replay: no hits → `verified`; hits → `rejected` (hits attached as evidence).
- no citations otherwise → `unverified` (post-processing already made these `unknown`).
- a verified `fail` on a critical check also faces the skeptic: refuted with valid citations →
  `disputed`.

`BudgetExceeded` stops every remaining LLM step: pending entailments become `unverified` with a
`skipped: budget` step; skipped skeptics leave the finding `verified` with
`VerificationStep(step="skeptic", passed=True, detail="skipped: budget")`. Steps run one at a
time in result order, so the outcome is reproducible.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from archlens.config import SessionLimits
from archlens.errors import BudgetExceeded
from archlens.evidence import SnippetReader
from archlens.llm.client import LLMClientProtocol
from archlens.models import (
    CheckResult,
    CheckSpec,
    CodeEvidence,
    Evidence,
    Finding,
    Rubric,
    Verification,
    VerificationStatus,
    VerificationStep,
    finding_id,
    is_scorable,
)
from archlens.tools.repo_tools import RepoTools
from archlens.verify.absence import Outcome, replay
from archlens.verify.entailment import EntailmentInput, EntailmentPrompts, batches, entail_batch
from archlens.verify.mechanical import check_citations
from archlens.verify.skeptic import SkepticPrompts, challenge

BUDGET_SKIP = "skipped: budget"
ABSENCE_STATUS: dict[Outcome, VerificationStatus] = {
    "absent": "verified",
    "found": "rejected",
    "inconclusive": "unverified",
}


@dataclass(frozen=True)
class VerifierOptions:
    """Ablation switches (EVALUATION.md §6): `entailment=False` is "mechanical only"."""

    entailment: bool = True
    skeptic: bool = True


@dataclass
class _State:
    result: CheckResult
    spec: CheckSpec | None
    status: VerificationStatus | None = None  # None: waiting for entailment
    steps: list[VerificationStep] = field(default_factory=list[VerificationStep])
    evidence: list[Evidence] | None = None  # replaces result.evidence when set

    def finish(self, status: VerificationStatus, step: VerificationStep | None = None) -> None:
        self.status = status
        if step is not None:
            self.steps.append(step)


class Verifier:
    def __init__(
        self,
        llm: LLMClientProtocol,
        tools: RepoTools,
        rubrics: Mapping[str, Rubric],
        commit_sha: str,
        *,
        entailment_prompts: EntailmentPrompts,
        skeptic_prompts: SkepticPrompts,
        skeptic_limits: SessionLimits,
        options: VerifierOptions | None = None,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.commit_sha = commit_sha
        self.entailment_prompts = entailment_prompts
        self.skeptic_prompts = skeptic_prompts
        self.skeptic_limits = skeptic_limits
        self.options = options or VerifierOptions()
        self.reader = SnippetReader(tools.root, tools.redactor)
        self.listing = {f.path: f for f in tools.listing()}
        self._specs = {(r.metric, c.id): c for r in rubrics.values() for c in r.checks}
        self.budget_exhausted = False

    async def verify(self, results: Sequence[CheckResult]) -> list[Finding]:
        """One `Finding` per result, in input order. Never raises for model-side problems."""
        states = [await self._route(r) for r in results]
        await self._entailment([s for s in states if s.status is None])
        if self.options.skeptic:
            for state in states:
                if self._needs_skeptic(state):
                    await self._skeptic(state)
        return [self._finding(s) for s in states]

    # --- routing (mechanical step, absence replay) ---

    async def _route(self, result: CheckResult) -> _State:
        state = _State(result, self._specs.get((result.metric, result.check_id)))
        if result.origin == "deterministic":
            state.finish("verified")
        elif result.verdict == "unknown":
            state.finish("unverified")
        elif state.spec is None:
            detail = f"{result.check_id} is not a check of the {result.metric} rubric"
            state.finish(
                "unverified", VerificationStep(step="mechanical", passed=False, detail=detail)
            )
        elif result.citations:
            mechanical = check_citations(
                result.citations,
                session_id=result.session_id,
                reader=self.reader,
                ledger=self.tools.ledger,
                listing=self.listing,
                existing=result.evidence,
            )
            state.steps.append(mechanical.step)
            if not mechanical.passed:
                state.finish("rejected")
            else:
                state.evidence = list(mechanical.evidence)
                if not self.options.entailment:
                    state.finish("verified")
        elif _absence_path(state.spec, result):
            absence = await replay(state.spec.absence_probes, self.tools, self.reader)
            state.evidence = list(absence.evidence)
            status = ABSENCE_STATUS[absence.outcome]
            state.finish(status, absence.step)
        else:
            detail = "no citations and the check does not allow evidence by absence"
            state.finish(
                "unverified", VerificationStep(step="mechanical", passed=False, detail=detail)
            )
        return state

    # --- entailment ---

    async def _entailment(self, pending: list[_State]) -> None:
        by_metric: dict[str, list[_State]] = {}
        for state in pending:
            by_metric.setdefault(state.result.metric, []).append(state)
        for metric, group in by_metric.items():
            items = {state.result.check_id: (state, _input(state)) for state in group}
            for batch in batches([item for _, item in items.values()]):
                if self.budget_exhausted:
                    for item in batch:
                        items[item.ref][0].finish("unverified", _entail_step(False, BUDGET_SKIP))
                    continue
                try:
                    answers = await entail_batch(
                        self.llm,
                        self.entailment_prompts,
                        batch,
                        boundary=self.tools.boundary,
                        metric=metric,
                    )
                except BudgetExceeded:
                    self.budget_exhausted = True
                    for item in batch:
                        items[item.ref][0].finish("unverified", _entail_step(False, BUDGET_SKIP))
                    continue
                for item in batch:
                    answer = answers[item.ref]
                    state = items[item.ref][0]
                    detail = f"{answer.supports or 'no answer'}: {answer.rationale}"
                    step = _entail_step(answer.supports == "yes", detail, answer.llm_call_id)
                    if answer.supports == "yes":
                        state.finish("verified", step)
                    elif answer.supports == "no":
                        state.finish("rejected", step)
                    else:
                        state.finish("unverified", step)

    # --- skeptic ---

    def _needs_skeptic(self, state: _State) -> bool:
        return (
            state.status == "verified"
            and state.result.origin == "llm"
            and state.result.verdict == "fail"
            and state.spec is not None
            and state.spec.severity == "critical"
        )

    async def _skeptic(self, state: _State) -> None:
        if self.budget_exhausted or state.spec is None:
            state.steps.append(VerificationStep(step="skeptic", passed=True, detail=BUDGET_SKIP))
            return
        evidence = [e for e in state.evidence or [] if isinstance(e, CodeEvidence)]
        outcome = await challenge(
            self.llm,
            self.skeptic_prompts,
            tools=self.tools,
            reader=self.reader,
            listing=self.listing,
            limits=self.skeptic_limits,
            check=state.spec,
            result=state.result,
            evidence=evidence,
        )
        state.steps.append(outcome.step)
        self.budget_exhausted = self.budget_exhausted or outcome.budget_exhausted
        if outcome.disputed:
            state.status = "disputed"

    # --- output ---

    def _finding(self, state: _State) -> Finding:
        result = state.result
        if state.evidence is not None:
            result = result.model_copy(update={"evidence": state.evidence})
        verification = Verification(status=state.status or "unverified", steps=state.steps)
        return Finding(
            id=finding_id(result.check_id, self.commit_sha),
            result=result,
            verification=verification,
            scored=is_scorable(result, verification),
        )


def _absence_path(spec: CheckSpec, result: CheckResult) -> bool:
    if result.verdict in ("fail", "partial"):
        return spec.evidence_policy == "absence_allowed"
    return result.verdict == "not_applicable" and spec.na_allowed


def _input(state: _State) -> EntailmentInput:
    evidence = [e for e in state.evidence or [] if isinstance(e, CodeEvidence)]
    title = state.spec.title if state.spec is not None else state.result.check_id
    return EntailmentInput(state.result.check_id, title, state.result, evidence)


def _entail_step(passed: bool, detail: str, call_id: str | None = None) -> VerificationStep:
    return VerificationStep(
        step="entailment", passed=passed, detail=detail[:300], llm_call_id=call_id
    )
