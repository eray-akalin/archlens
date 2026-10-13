"""What every stage of one run shares (ARCHITECTURE.md §4)."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from archlens.config import AppConfig
from archlens.index.embed import Embedder
from archlens.llm.client import LLMClientProtocol
from archlens.models import AssessmentReport, Rubric
from archlens.storage import Storage
from archlens.verify.pipeline import VerifierOptions


@dataclass(frozen=True)
class RunOptions:
    target: str  # local path or https URL
    ref: str | None = None
    metrics: tuple[str, ...] | None = None  # None → every loaded rubric
    scanners: bool = True
    verifier: VerifierOptions = field(default_factory=VerifierOptions)
    # reuse a metric's LLM results when its inputs are unchanged (PR mode); off by default, since
    # repeated eval runs must stay independent samples
    metric_cache: bool = False
    base: AssessmentReport | None = None  # set → also write `pr_comment.md` against it
    # ablation switches (EVALUATION.md §6); the verifier's are in `verifier`
    facts_in_context: bool = True  # False: the evaluator sees no facts (prompt and get_facts)
    deterministic_rules: bool = True  # False: deterministic checks are judged by the evaluator
    self_consistency: bool = True  # False: one evaluator run per check


@dataclass(frozen=True)
class RunContext:
    run_id: str
    config: AppConfig
    storage: Storage
    llm: LLMClientProtocol
    embedder: Embedder
    boundary: str  # the run's repo_data boundary; the LLM client must use the same one
    work_dir: Path  # per-run local files (the index); outlives the clone
    tools_dir: Path
    rubrics: dict[str, Rubric]
    prompts_dir: Path = Path("prompts")
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
