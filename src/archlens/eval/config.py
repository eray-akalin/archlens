"""Eval configs (`eval/configs/<name>.yaml`, EVALUATION.md §6-7) and the per-run result records."""

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import Field, ValidationError

from archlens.errors import ConfigError
from archlens.eval.repos import DEFAULT_REPOS_FILE
from archlens.eval.variants import DEFAULT_VARIANTS_FILE
from archlens.models import AssessmentReport, Contract, Verdict

BASE = "base"


class Features(Contract):
    """Pipeline switches for the ablation (EVALUATION.md §6); `full` = all defaults."""

    facts_in_context: bool = True
    deterministic_rules: bool = True
    verifier: Literal["full", "mechanical"] = "full"
    self_consistency: bool = True
    skeptic: bool = True

    def unsupported(self) -> list[str]:
        """Switches the pipeline can't honour yet (the ablation is M5.1)."""
        missing = [] if self.facts_in_context else ["facts_in_context: false"]
        missing += [] if self.deterministic_rules else ["deterministic_rules: false"]
        missing += [] if self.self_consistency else ["self_consistency: false"]
        return missing


class SuiteEntry(Contract):
    repo: str
    variant: str = BASE  # "base" (the clean repo) or a variant id from the variants file
    runs: Annotated[int, Field(ge=1)] = 1


class EvalConfig(Contract):
    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]*$")]
    budget_usd: Annotated[float, Field(gt=0)]
    seed: int = 0
    repos_file: Path = DEFAULT_REPOS_FILE
    variants_file: Path = DEFAULT_VARIANTS_FILE
    features: Features = Field(default_factory=Features)
    suite: list[SuiteEntry]


def load_eval_config(path: Path) -> EvalConfig:
    """Raises ConfigError."""
    try:
        return EvalConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise ConfigError(str(path), "eval config", str(exc)) from exc


class ChangedRange(Contract):
    path: str
    start_line: int
    end_line: int


class AppliedRecord(Contract):
    id: str
    expected: dict[str, Verdict]
    may_affect: list[str]
    absence: bool
    injection: bool
    changed: list[ChangedRange]


class VariantRun(Contract):
    """One assessment of the eval: `variants/<name>.json`."""

    name: str  # "<repo>.<variant>.<index>"
    repo: str
    variant: str
    index: int
    commit: str  # "<base sha>+<variant>" (EVALUATION.md §7)
    mutations: list[AppliedRecord]
    skipped: list[str]  # mutations whose precondition did not hold
    seconds: float
    report: AssessmentReport
