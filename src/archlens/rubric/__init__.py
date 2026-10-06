"""Rubrics: YAML loader, deterministic rule registry and `RuleContext` (docs/RUBRICS.md)."""

from archlens.rubric import rules  # importing registers the built-in rules
from archlens.rubric.context import RuleContext
from archlens.rubric.loader import DEFAULT_RUBRICS_DIR, load_rubric, load_rubrics
from archlens.rubric.registry import (
    RegisteredRule,
    RuleParams,
    get_rule,
    registered_rules,
    rule,
    run_rule,
)

__all__ = [
    "DEFAULT_RUBRICS_DIR",
    "RegisteredRule",
    "RuleContext",
    "RuleParams",
    "get_rule",
    "load_rubric",
    "load_rubrics",
    "registered_rules",
    "rule",
    "rules",
    "run_rule",
]
