"""Rubric loading (RUBRICS.md §1): YAML → validated `Rubric`.

Rejected with `RubricError` (naming the file and the problem): invalid YAML, unknown keys or
missing fields (the `Rubric`/`CheckSpec` models, incl. missing absence probes), a `metric` that
differs from the file name stem, and — for non-retired deterministic checks — unknown rule names
and params the rule's `Params` model rejects. Unknown profile flags in `applies_when` are only
logged: a typo makes a check never apply, which the shipped-rubrics test catches.
"""

import logging
from pathlib import Path

import yaml
from pydantic import ValidationError

from archlens.errors import RubricError
from archlens.models import AppliesWhen, Rubric
from archlens.profile import FLAGS
from archlens.rubric.registry import get_rule

logger = logging.getLogger(__name__)

DEFAULT_RUBRICS_DIR = Path("rubrics")


def load_rubric(path: Path) -> Rubric:
    """Parse and validate one rubric file. Raises RubricError."""
    try:
        data: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RubricError(f"{path}: cannot read YAML: {exc}") from exc
    try:
        rubric = Rubric.model_validate(data)
    except ValidationError as exc:
        raise RubricError(f"{path}: {describe(exc)}") from exc
    if rubric.metric != path.stem:
        raise RubricError(f"{path}: metric {rubric.metric!r} must equal the file name stem")
    for check in rubric.checks:
        if check.retired or check.type != "deterministic":
            continue
        registered = get_rule(check.rule or "")
        if registered is None:
            raise RubricError(f"{path}: {check.id}: unknown rule {check.rule!r}")
        try:
            registered.parse_params(check.params)
        except ValidationError as exc:
            raise RubricError(f"{path}: {check.id}: params: {describe(exc)}") from exc
    _warn_unknown_flags(path, rubric)
    return rubric


def load_rubrics(directory: Path = DEFAULT_RUBRICS_DIR) -> dict[str, Rubric]:
    """Every `*.yaml` in `directory` except `_*` files, keyed by metric in sorted order.

    Raises RubricError for the first invalid file.
    """
    paths = sorted(p for p in directory.glob("*.yaml") if not p.name.startswith("_"))
    return {rubric.metric: rubric for rubric in map(load_rubric, paths)}


def describe(exc: ValidationError) -> str:
    """One line per pydantic error: `loc.path: message`."""
    return "; ".join(
        f"{'.'.join(str(part) for part in err['loc']) or '<root>'}: {err['msg']}"
        for err in exc.errors()
    )


def _warn_unknown_flags(path: Path, rubric: Rubric) -> None:
    conditions: list[tuple[str, AppliesWhen]] = [(rubric.metric, rubric.applies_when)]
    conditions += [(check.id, check.applies_when) for check in rubric.checks]
    for owner, cond in conditions:
        unknown = sorted(set(cond.all + cond.any + cond.none) - set(FLAGS))
        if unknown:
            logger.warning("%s: %s: unknown profile flags %s", path, owner, unknown)
