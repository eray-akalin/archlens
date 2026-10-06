"""Deterministic rule registry (RUBRICS.md §5).

A rule is a pure function `(ctx: RuleContext, params: P) -> RuleOutcome` registered with
`@rule("<family>.<name>")`; its `Params` model (a `RuleParams` subclass, taken from the `params`
annotation) holds the defaults and validates the YAML `params` block. `run_rule` wraps the outcome
into a `CheckResult`; it never raises — any problem becomes `verdict="unknown"` with a reason.
"""

import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import cast, get_type_hints

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from archlens.models import CheckResult, CheckSpec, Rubric, RuleOutcome
from archlens.rubric.context import RuleContext

logger = logging.getLogger(__name__)

_NAME = re.compile(r"^[a-z][a-z_]*\.[a-z][a-z_]*$")


class RuleParams(BaseModel):
    """Base for every rule's `Params` model: immutable, unknown keys rejected (catches typos)."""

    model_config = ConfigDict(frozen=True, extra="forbid")


@dataclass(frozen=True)
class RegisteredRule:
    name: str
    params_model: type[RuleParams]
    func: Callable[[RuleContext, RuleParams], RuleOutcome]

    def parse_params(self, raw: Mapping[str, JsonValue]) -> RuleParams:
        """Validated params (defaults filled in). Raises pydantic.ValidationError."""
        return self.params_model.model_validate(dict(raw))


_REGISTRY: dict[str, RegisteredRule] = {}


def rule[P: RuleParams](
    name: str,
) -> Callable[[Callable[[RuleContext, P], RuleOutcome]], Callable[[RuleContext, P], RuleOutcome]]:
    """Register a rule under `name` (`<family>.<name>`). Raises ValueError/TypeError at import
    time for a malformed or duplicate name or a missing/invalid `params` annotation."""
    if not _NAME.match(name):
        raise ValueError(f"rule name must look like '<family>.<name>': {name!r}")

    def register(
        func: Callable[[RuleContext, P], RuleOutcome],
    ) -> Callable[[RuleContext, P], RuleOutcome]:
        params_model: object = get_type_hints(func).get("params")
        if not (isinstance(params_model, type) and issubclass(params_model, RuleParams)):
            raise TypeError(f"rule {name}: `params` must be annotated with a RuleParams subclass")
        if name in _REGISTRY:
            raise ValueError(f"rule {name} registered twice")
        _REGISTRY[name] = RegisteredRule(
            name, params_model, cast(Callable[[RuleContext, RuleParams], RuleOutcome], func)
        )
        return func

    return register


def get_rule(name: str) -> RegisteredRule | None:
    return _REGISTRY.get(name)


def registered_rules() -> Mapping[str, RegisteredRule]:
    return dict(_REGISTRY)


def run_rule(check: CheckSpec, rubric: Rubric, ctx: RuleContext) -> CheckResult:
    """Run a deterministic check and wrap the outcome (`origin="deterministic"`,
    `confidence="high"`). An unknown rule, invalid params or an exception raised by the rule
    yields `verdict="unknown"` with the reason; never raises."""
    outcome = _outcome(check, ctx)
    return CheckResult(
        check_id=check.id,
        metric=rubric.metric,
        origin="deterministic",
        verdict=outcome.verdict,
        claim=outcome.claim,
        evidence=outcome.evidence,
        confidence="high",
        reason=outcome.reason,
        rubric_version=rubric.version,
    )


def _outcome(check: CheckSpec, ctx: RuleContext) -> RuleOutcome:
    registered = get_rule(check.rule or "")
    if registered is None:
        return _unknown(f"unknown rule {check.rule!r}", "rule_unknown")
    try:
        params = registered.parse_params(check.params)
    except ValidationError as exc:
        return _unknown(
            f"invalid params for {registered.name}: {exc.error_count()} error(s)", "params_invalid"
        )
    try:
        return registered.func(ctx, params)
    except Exception as exc:  # a broken rule must not fail the run (CLAUDE.md: errors)
        logger.exception("rule %s failed for %s", registered.name, check.id)
        return _unknown(f"rule {registered.name} failed: {type(exc).__name__}", "rule_error")


def _unknown(claim: str, reason: str) -> RuleOutcome:
    return RuleOutcome(verdict="unknown", claim=claim, evidence=[], reason=reason)
