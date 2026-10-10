"""Runtime settings (env vars, prefix ARCHLENS_) and loaders for `config/*.yaml`.

Every failure is raised as `ConfigError` naming the env var or YAML field at fault. Settings read
`.env` by default; tests pass `env_file=None` so a developer's `.env` never leaks into them.
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from archlens.errors import ConfigError

ENV_PREFIX = "ARCHLENS_"
DEFAULT_ENV_FILE = Path(".env")


class Settings(BaseSettings):
    """Process configuration from the environment (see `.env.example`). Secrets are SecretStr."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX, env_file=DEFAULT_ENV_FILE, env_ignore_empty=True, extra="ignore"
    )

    # LLM provider (docs/LLM.md §1)
    llm_base_url: str | None = None
    llm_auth: Literal["key", "entra"] = "key"
    llm_api_key: SecretStr | None = None
    llm_key_header: str | None = None
    llm_record_mode: Literal["off", "record", "replay"] = "off"
    # Deployment overrides per role; None → config/models.yaml
    model_evaluator: str | None = None
    model_verifier: str | None = None
    model_skeptic: str | None = None
    model_tiebreak: str | None = None
    model_synth: str | None = None
    model_embed: str | None = None
    # Limits & budget
    max_concurrency: Annotated[int, Field(ge=1)] = 4
    tpm_limit: Annotated[int, Field(ge=1)] = 200_000
    run_budget_usd: Annotated[float, Field(gt=0)] = 1.00
    # Semgrep registry rules are licensed for internal use only (no redistribution, no use as a
    # service): false in the hosted image, which then runs no semgrep at all (docs/AZURE.md §4).
    semgrep_registry_rules: bool = True
    # Storage
    storage: Literal["local", "azure"] = "local"
    data_dir: Path = Path(".archlens")
    config_dir: Path = Path("config")
    # Pinned rule packs and other tool data (scripts/install_tools.sh)
    tools_dir: Path = Field(default_factory=lambda: Path.home() / ".cache" / "archlens" / "tools")
    azure_storage_account: str | None = None
    azure_cosmos_endpoint: str | None = None
    azure_queue_name: str = "assessments"
    # Hosted API (M4); comma-separated in the environment
    api_keys: Annotated[list[SecretStr], NoDecode] = Field(default_factory=list[SecretStr])
    allowed_repo_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["github.com"]
    )
    max_concurrent_runs_per_key: Annotated[int, Field(ge=1)] = 1
    runs_per_day_per_key: Annotated[int, Field(ge=0)] = 10
    # Telemetry
    otel_enabled: bool = False
    applicationinsights_connection_string: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("APPLICATIONINSIGHTS_CONNECTION_STRING")
    )

    @field_validator("api_keys", "allowed_repo_hosts", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


# --- config/*.yaml ----------------------------------------------------------------------------


class _ConfigFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RoleConfig(_ConfigFile):
    deployment: str
    reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = None
    temperature: Annotated[float, Field(ge=0, le=2)] | None = None
    max_completion_tokens: Annotated[int, Field(ge=1)] | None = None


class Roles(_ConfigFile):
    evaluator: RoleConfig
    verifier: RoleConfig
    skeptic: RoleConfig
    synth: RoleConfig
    embed: RoleConfig
    # the self-consistency tie-break session (LLM.md §4); None → the evaluator's settings
    tiebreak: RoleConfig | None = None

    def get(self, name: str) -> RoleConfig | None:
        """The role's settings (`tiebreak` falls back to `evaluator`); None for an unknown name."""
        if name == "tiebreak":
            return self.tiebreak or self.evaluator
        role = getattr(self, name, None) if name in type(self).model_fields else None
        return role if isinstance(role, RoleConfig) else None

    def all(self) -> list[RoleConfig]:
        return [r for r in (self.evaluator, self.verifier, self.skeptic, self.synth, self.embed,
                            self.tiebreak) if r is not None]  # fmt: skip


class SessionLimits(_ConfigFile):
    max_tool_calls: Annotated[int, Field(ge=1)]
    max_context_tokens: Annotated[int, Field(ge=1)]
    max_facts: Annotated[int, Field(ge=1)] | None = None


class Sessions(_ConfigFile):
    evaluator: SessionLimits
    skeptic: SessionLimits


class TokenEstimate(_ConfigFile):
    input: Annotated[int, Field(ge=0)]
    cached_input: Annotated[int, Field(ge=0)] = 0
    output: Annotated[int, Field(ge=0)]


class Estimates(_ConfigFile):
    tokens_per_metric: TokenEstimate
    verifier_tokens_per_finding: TokenEstimate


class ModelsConfig(_ConfigFile):
    """`config/models.yaml`: role routing, session budgets, dry-run estimates."""

    roles: Roles
    sessions: Sessions
    estimates: Estimates


class Price(_ConfigFile):
    """USD per 1M tokens."""

    input: Annotated[float, Field(ge=0)]
    cached_input: Annotated[float, Field(ge=0)]
    output: Annotated[float, Field(ge=0)]


class PricingConfig(_ConfigFile):
    """`config/pricing.yaml`, keyed by deployment name."""

    currency: Literal["USD"]
    last_checked: date
    deployments: dict[str, Price]


class ToolConfig(_ConfigFile):
    version: str
    checksum: str | None = None
    timeout_s: Annotated[int, Field(ge=1)]
    rulesets: list[str] | None = None
    metrics: str | None = None


class ToolsConfig(_ConfigFile):
    """`config/tools.yaml`: pinned scanner versions and timeouts."""

    tools: dict[str, ToolConfig]
    # never run (no run record, so the rules that need them answer `unknown`, `tool_not_run`)
    disabled: list[str] = Field(default_factory=list[str])


@dataclass(frozen=True)
class AppConfig:
    settings: Settings
    models: ModelsConfig  # ARCHLENS_MODEL_<ROLE> overrides already applied
    pricing: PricingConfig
    tools: ToolsConfig


def load_settings(env_file: Path | None = DEFAULT_ENV_FILE) -> Settings:
    """Settings from the environment and `env_file` (None = environment only).

    Raises ConfigError naming the env var, e.g. `ARCHLENS_MAX_CONCURRENCY`.
    """
    try:
        # `_env_file` is a real BaseSettings init argument that pyright's synthesized
        # dataclass-transform signature doesn't include.
        return Settings(_env_file=env_file)  # pyright: ignore[reportCallIssue]
    except ValidationError as exc:
        raise _config_error("environment", exc, as_env_var=True) from exc


def load_config(settings: Settings | None = None) -> AppConfig:
    """Load and cross-check `models.yaml`, `pricing.yaml` and `tools.yaml` from `config_dir`.

    Raises ConfigError for a missing file, invalid YAML, a missing/invalid/unknown field, or a
    deployment without a price (cost accounting and the budget guard depend on it).
    """
    settings = settings if settings is not None else load_settings()
    config_dir = settings.config_dir
    models = _apply_deployment_overrides(_parse(ModelsConfig, config_dir / "models.yaml"), settings)
    pricing = _parse(PricingConfig, config_dir / "pricing.yaml")
    tools = _parse(ToolsConfig, config_dir / "tools.yaml")
    if not settings.semgrep_registry_rules and "semgrep" not in tools.disabled:
        tools = tools.model_copy(update={"disabled": [*tools.disabled, "semgrep"]})
    _check_prices(models, pricing, source=str(config_dir / "pricing.yaml"))
    return AppConfig(settings=settings, models=models, pricing=pricing, tools=tools)


def _parse[M: BaseModel](model: type[M], path: Path) -> M:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(str(path), "<file>", "file not found") from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(str(path), "<file>", f"invalid YAML: {exc}") from exc
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise _config_error(str(path), exc) from exc


def _config_error(source: str, exc: ValidationError, *, as_env_var: bool = False) -> ConfigError:
    errors = exc.errors()
    loc = [str(part) for part in errors[0]["loc"]]
    field = ".".join(loc) or "<root>"
    if as_env_var and loc:
        # Aliased fields (APPLICATIONINSIGHTS_...) already carry their env var name.
        field = loc[0] if loc[0].isupper() else ENV_PREFIX + loc[0].upper()
    more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
    return ConfigError(source, field, errors[0]["msg"] + more)


def _apply_deployment_overrides(models: ModelsConfig, settings: Settings) -> ModelsConfig:
    overrides = {
        "evaluator": settings.model_evaluator,
        "verifier": settings.model_verifier,
        "skeptic": settings.model_skeptic,
        "synth": settings.model_synth,
        "embed": settings.model_embed,
        "tiebreak": settings.model_tiebreak,
    }
    roles = models.roles
    updated = {
        role: base.model_copy(update={"deployment": deployment})
        for role, deployment in overrides.items()
        if deployment and (base := roles.get(role)) is not None
    }
    return models.model_copy(update={"roles": roles.model_copy(update=updated)})


def _check_prices(models: ModelsConfig, pricing: PricingConfig, *, source: str) -> None:
    roles = models.roles
    used = {r.deployment for r in roles.all()}
    missing = sorted(used - pricing.deployments.keys())
    if missing:
        raise ConfigError(
            source,
            f"deployments.{missing[0]}",
            f"no price for deployment(s) {missing}; add them so cost accounting and the budget "
            "guard work",
        )
