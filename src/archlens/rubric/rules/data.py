"""`data.*` rules over the snapshot listing (DATA-01)."""

from pydantic import Field

from archlens.models import RuleOutcome
from archlens.rubric.context import RuleContext
from archlens.rubric.registry import RuleParams, rule
from archlens.rubric.rules.files import AnyExistsParams, any_exists

MIGRATION_GLOBS = [
    "**/alembic/versions/**", "**/migrations/*.py", "**/migrations/*.sql", "**/db/migrate/**",
    "**/db/migration/**", "**/flyway/**", "**/liquibase/**", "**/Migrations/*.cs",
    "**/prisma/migrations/**",
]  # fmt: skip


class MigrationsParams(RuleParams):
    globs: list[str] = Field(default_factory=lambda: list(MIGRATION_GLOBS), min_length=1)


@rule("data.migrations_present")
def migrations_present(ctx: RuleContext, params: MigrationsParams) -> RuleOutcome:
    """pass if any listed path matches a migration glob, else fail."""
    outcome = any_exists(ctx, AnyExistsParams(globs=params.globs))
    if outcome.verdict == "fail":
        return outcome.model_copy(update={"claim": "No schema migrations found."})
    return outcome
