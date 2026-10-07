"""`archlens assess --dry-run`: projected LLM cost before spending anything (docs/LLM.md §9).

Heuristics from `config/models.yaml` (`estimates`) until recorded runs exist: per metric with LLM
checks, one evaluator session (plus one consistency rerun and one tie-break session when it has a
critical LLM check), one skeptic session per critical LLM check (a third of a session), one
entailment estimate per LLM check, and one synthesizer call. Every LLM check counts as applicable,
so this is an upper bound. Embeddings are not included (text-embedding-3-small costs about $0.02
per million tokens).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from archlens.config import AppConfig, Price, TokenEstimate
from archlens.models import Rubric

SYNTH_TOKENS = TokenEstimate(input=6000, cached_input=0, output=1200)
SKEPTIC_SHARE = Decimal(1) / Decimal(3)
MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class ProjectionLine:
    item: str
    usd: Decimal


@dataclass(frozen=True)
class Projection:
    lines: list[ProjectionLine]

    @property
    def total(self) -> Decimal:
        return sum((line.usd for line in self.lines), Decimal(0))


def tokens_cost(price: Price, tokens: TokenEstimate, times: Decimal = Decimal(1)) -> Decimal:
    uncached = max(tokens.input - tokens.cached_input, 0)
    usd = (
        Decimal(uncached) * Decimal(str(price.input))
        + Decimal(tokens.cached_input) * Decimal(str(price.cached_input))
        + Decimal(tokens.output) * Decimal(str(price.output))
    ) / MILLION
    return usd * times


def project_cost(config: AppConfig, rubrics: Sequence[Rubric]) -> Projection:
    models, pricing = config.models, config.pricing
    price = {
        role: pricing.deployments[(models.roles.get(role) or models.roles.evaluator).deployment]
        for role in ("evaluator", "verifier", "skeptic", "synth", "tiebreak")
    }
    estimates = models.estimates
    lines: list[ProjectionLine] = []
    for rubric in rubrics:
        llm = [c for c in rubric.checks if c.type == "llm" and not c.retired]
        if not llm:
            continue
        critical = [c for c in llm if c.consistency_runs > 1]
        sessions = Decimal(1 + (1 if critical else 0))
        usd = tokens_cost(price["evaluator"], estimates.tokens_per_metric, sessions)
        if critical:  # worst case: the two runs disagree and a tie-break session follows
            usd += tokens_cost(price["tiebreak"], estimates.tokens_per_metric)
        usd += tokens_cost(
            price["skeptic"], estimates.tokens_per_metric, SKEPTIC_SHARE * len(critical)
        )
        usd += tokens_cost(
            price["verifier"], estimates.verifier_tokens_per_finding, Decimal(len(llm))
        )
        lines.append(ProjectionLine(f"{rubric.metric} ({len(llm)} LLM checks)", usd))
    if lines:
        lines.append(ProjectionLine("report narrative", tokens_cost(price["synth"], SYNTH_TOKENS)))
    return Projection(lines)
