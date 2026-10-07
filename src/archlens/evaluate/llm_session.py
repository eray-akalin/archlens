"""One evaluator session per metric over its LLM checks (docs/LLM.md §3-4).

Prompt layout is static-first: system prompt, rubric text (identical across repos for a rubric
version), tool definitions, then repository content — the profile and the selected facts, both
wrapped as untrusted data, with the facts' evidence lines marked in the session's ledger before
the first turn. The model's answer is post-processed deterministically into `CheckResult`s with
raw citations (`postprocess`); citations are validated later by the verifier.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from archlens.config import SessionLimits
from archlens.facts.extractors.injection import KIND as INJECTION
from archlens.llm.client import LLMClientProtocol
from archlens.llm.prompts import DEFAULT_PROMPTS_DIR, Prompt, joined_version, load_prompt
from archlens.llm.session import SessionError, run_tool_session
from archlens.llm.untrusted import wrap
from archlens.models import (
    CheckResult,
    CheckSpec,
    Citation,
    Confidence,
    Fact,
    FactSet,
    LLMCheckOutput,
    MetricEvaluationOutput,
    RepoProfile,
    Rubric,
    SearchRecord,
    Verdict,
    severity_rank,
)
from archlens.tools.repo_tools import RepoTools, fact_path

STAGE = "evaluate"
MAX_CLAIM_CHARS = 300
MAX_CITATIONS = 5
FAILURE_CLAIMS: dict[SessionError, str] = {
    "invalid_output": "The evaluator's answer could not be used, even after a retry.",
    "budget": "The run budget was exhausted before this check was evaluated.",
    "llm_error": "The model call failed after retries.",
}


@dataclass(frozen=True)
class EvaluatorPrompts:
    system: Prompt
    metric: Prompt
    repo: Prompt

    @classmethod
    def load(cls, directory: Path = DEFAULT_PROMPTS_DIR) -> "EvaluatorPrompts":
        return cls(
            *(
                load_prompt(directory / f"evaluator.{part}.md")
                for part in ("system", "metric", "repo")
            )
        )

    @property
    def version(self) -> str:
        """`prompt_version` of the session: the three versions joined with `;`."""
        return joined_version(self.system, self.metric, self.repo)


@dataclass(frozen=True)
class SessionInfo:
    """Fields shared by every result of one session."""

    metric: str
    rubric_version: str
    session_id: str
    prompt_version: str
    model: str | None
    attempt: int
    search_log: tuple[SearchRecord, ...]
    llm_call_ids: tuple[str, ...]


@dataclass(frozen=True)
class Evaluator:
    llm: LLMClientProtocol
    tools: RepoTools
    profile: RepoProfile
    facts: FactSet
    limits: SessionLimits
    prompts: EvaluatorPrompts

    async def evaluate(
        self,
        rubric: Rubric,
        checks: Sequence[CheckSpec],
        *,
        attempt: int = 0,
        role: str = "evaluator",
    ) -> list[CheckResult]:
        """One session over `checks` (LLM checks of `rubric`); one result per check, in order.
        `role="tiebreak"` runs it on the tie-break model (config/models.yaml). Never raises for
        model-side problems (they become `unknown` with a reason)."""
        if not checks:
            return []
        session = self.tools.session(f"{rubric.metric}:{STAGE}:{attempt}")
        wanted = dict.fromkeys(k for check in checks for k in check.fact_kinds)
        kinds = [k for k in wanted if k != INJECTION]
        # injection_attempt facts go to every session, ahead of the cap, so the model is told which
        # repository text is trying to steer it; the header names the kind only when there are some
        flagged = self.facts.by_kind(INJECTION)
        limit = max((self.limits.max_facts or 150) - len(flagged), 0)
        selected, total = select_facts(self.facts, kinds, limit)
        selected, total = [*flagged, *selected], total + len(flagged)
        kinds = [*kinds, INJECTION] if flagged else kinds
        repo_text = self.prompts.repo.render(
            profile=wrap(profile_summary(self.profile), "profile", self.tools.boundary),
            facts=session.show_facts(selected),
            shown=len(selected),
            total=total,
            kinds=kinds,
            attempt=attempt,
            check_ids=[c.id for c in checks],
        )
        system = self.prompts.system.render(max_tool_calls=self.limits.max_tool_calls)
        outcome = await run_tool_session(
            self.llm,
            role=role,
            stage=STAGE,
            prompt_version=self.prompts.version,
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": self.prompts.metric.render(rubric=rubric, checks=checks),
                },
                {"role": "user", "content": repo_text},
            ],
            response_format=MetricEvaluationOutput,
            tools=session,
            limits=self.limits,
            metric=rubric.metric,
            tags=tuple(c.id for c in checks),
        )
        info = SessionInfo(
            metric=rubric.metric,
            rubric_version=rubric.version,
            session_id=session.session_id,
            prompt_version=self.prompts.version,
            model=outcome.records[-1].model if outcome.records else None,
            attempt=attempt,
            search_log=tuple(session.search_log),
            llm_call_ids=tuple(r.id for r in outcome.records),
        )
        if outcome.parsed is None:
            return failed(checks, info, outcome.error or "llm_error")
        return postprocess(outcome.parsed.results, checks, info)


def postprocess(
    outputs: Sequence[LLMCheckOutput], checks: Sequence[CheckSpec], info: SessionInfo
) -> list[CheckResult]:
    """docs/LLM.md §4: drop unknown/duplicate check IDs, add `missing_from_output` for missing
    ones, coerce NA without `na_allowed` (`na_not_allowed`) and evidence-less verdicts other than
    the allowed ones (`no_evidence`) to unknown; trim claims to 300 chars and citations to 5."""
    wanted = {c.id for c in checks}
    by_id: dict[str, LLMCheckOutput] = {}
    for out in outputs:
        if out.check_id in wanted:
            by_id.setdefault(out.check_id, out)
    results: list[CheckResult] = []
    for check in checks:
        out = by_id.get(check.id)
        if out is None:
            claim = "The evaluator returned no result for this check."
            results.append(_result(check, info, "unknown", claim, [], "low", "missing_from_output"))
            continue
        verdict: Verdict = out.verdict
        reason: str | None = None
        citations = list(out.citations[:MAX_CITATIONS])
        if verdict == "not_applicable" and not check.na_allowed:
            verdict, reason = "unknown", "na_not_allowed"
        elif verdict == "unknown":
            reason = "model_unknown"
        elif not citations and not _evidence_less_allowed(check, verdict):
            verdict, reason = "unknown", "no_evidence"
        confidence: Confidence = out.confidence if verdict == out.verdict else "low"
        results.append(
            _result(check, info, verdict, _trim(out.claim), citations, confidence, reason)
        )
    return results


def failed(
    checks: Sequence[CheckSpec], info: SessionInfo, error: SessionError
) -> list[CheckResult]:
    """Every check `unknown` with the session's failure as the reason."""
    return [_result(c, info, "unknown", FAILURE_CLAIMS[error], [], "low", error) for c in checks]


def select_facts(facts: FactSet, kinds: Sequence[str], limit: int) -> tuple[list[Fact], int]:
    """Facts of `kinds`, at most `limit`: most severe first, then round-robin over the paths
    they concern so many files are represented. Returns (selected, total of those kinds)."""
    pool = facts.by_kind(*kinds) if kinds else []
    tiers: dict[int, dict[str, list[Fact]]] = {}
    for fact in pool:
        tiers.setdefault(severity_rank(fact.severity), {}).setdefault(
            fact_path(fact) or "", []
        ).append(fact)
    selected: list[Fact] = []
    for rank in sorted(tiers, reverse=True):
        queues = list(tiers[rank].values())
        while queues and len(selected) < limit:
            for queue in list(queues):
                selected.append(queue.pop(0))
                if not queue:
                    queues.remove(queue)
                if len(selected) == limit:
                    break
    return selected, len(pool)


def profile_summary(profile: RepoProfile) -> str:
    """Plain-text profile for the prompt (wrapped as repository data by the caller)."""
    languages = ", ".join(f"{name} {loc} LOC" for name, loc in list(profile.languages.items())[:6])
    flags = ", ".join(sorted(flag for flag, on in profile.flags.items() if on))
    rows = [
        ("languages", languages),
        ("frameworks", ", ".join(profile.frameworks)),
        ("package managers", ", ".join(profile.package_managers)),
        ("CI systems", ", ".join(profile.ci_systems)),
        ("test frameworks", ", ".join(profile.test_frameworks)),
        ("flags", flags),
        ("entrypoints", ", ".join(profile.entrypoints[:10])),
    ]
    return "\n".join(f"{name}: {value or '-'}" for name, value in rows)


def _evidence_less_allowed(check: CheckSpec, verdict: Verdict) -> bool:
    if verdict in ("fail", "partial"):
        return check.evidence_policy == "absence_allowed"
    return verdict == "not_applicable" and check.na_allowed


def _trim(claim: str) -> str:
    claim = " ".join(claim.split())
    return claim if len(claim) <= MAX_CLAIM_CHARS else claim[: MAX_CLAIM_CHARS - 1] + "…"


def _result(
    check: CheckSpec,
    info: SessionInfo,
    verdict: Verdict,
    claim: str,
    citations: list[Citation],
    confidence: Confidence,
    reason: str | None,
) -> CheckResult:
    return CheckResult(
        check_id=check.id,
        metric=info.metric,
        origin="llm",
        verdict=verdict,
        claim=claim,
        citations=citations,
        confidence=confidence,
        reason=reason,
        search_log=list(info.search_log),
        llm_call_ids=list(info.llm_call_ids),
        session_id=info.session_id,
        rubric_version=info.rubric_version,
        prompt_version=info.prompt_version,
        model=info.model,
        attempt=info.attempt,
    )
