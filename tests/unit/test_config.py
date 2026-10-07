import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from archlens.config import Settings, load_config, load_settings
from archlens.errors import ConfigError

REPO_CONFIG = Path(__file__).parents[2] / "config"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Hide the developer's ARCHLENS_* variables so results don't depend on the machine, and drop
    the ones `settings_for` set so they can't leak into later tests (monkeypatch then restores
    the hidden ones)."""
    for name in list(os.environ):
        if name.startswith("ARCHLENS_") or name == "APPLICATIONINSIGHTS_CONNECTION_STRING":
            monkeypatch.delenv(name)
    yield
    for name in [n for n in os.environ if n.startswith("ARCHLENS_")]:
        del os.environ[name]


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    target = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, target)
    return target


def settings_for(config_dir: Path, **env: str) -> Settings:
    os.environ.update({f"ARCHLENS_{k.upper()}": v for k, v in env.items()})
    os.environ["ARCHLENS_CONFIG_DIR"] = str(config_dir)
    return load_settings(env_file=None)


# --- Settings ---------------------------------------------------------------------------------


def test_defaults_without_environment() -> None:
    settings = load_settings(env_file=None)
    assert settings.llm_auth == "key"
    assert settings.llm_base_url is None
    assert settings.max_concurrency == 4
    assert settings.run_budget_usd == 1.00
    assert settings.allowed_repo_hosts == ["github.com"]
    assert settings.api_keys == []


def test_environment_values_are_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCHLENS_LLM_AUTH", "entra")
    monkeypatch.setenv("ARCHLENS_MAX_CONCURRENCY", "8")
    monkeypatch.setenv("ARCHLENS_ALLOWED_REPO_HOSTS", "github.com, gitlab.com")
    monkeypatch.setenv("ARCHLENS_API_KEYS", "k1,k2")
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "InstrumentationKey=abc")
    settings = load_settings(env_file=None)
    assert settings.llm_auth == "entra"
    assert settings.max_concurrency == 8
    assert settings.allowed_repo_hosts == ["github.com", "gitlab.com"]
    assert [k.get_secret_value() for k in settings.api_keys] == ["k1", "k2"]
    conn = settings.applicationinsights_connection_string
    assert conn is not None and conn.get_secret_value() == "InstrumentationKey=abc"


def test_empty_values_count_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCHLENS_LLM_API_KEY", "")
    monkeypatch.setenv("ARCHLENS_LLM_KEY_HEADER", "")
    settings = load_settings(env_file=None)
    assert settings.llm_api_key is None
    assert settings.llm_key_header is None


def test_secrets_are_masked_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCHLENS_LLM_API_KEY", "sk-very-secret")
    settings = load_settings(env_file=None)
    assert "sk-very-secret" not in repr(settings)
    assert "sk-very-secret" not in str(settings.model_dump())


def test_env_file_is_read_only_when_asked(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ARCHLENS_MAX_CONCURRENCY=2\n")
    assert load_settings(env_file=env_file).max_concurrency == 2
    assert load_settings(env_file=None).max_concurrency == 4


@pytest.mark.parametrize(
    ("var", "value"),
    [
        ("ARCHLENS_MAX_CONCURRENCY", "many"),
        ("ARCHLENS_MAX_CONCURRENCY", "0"),
        ("ARCHLENS_LLM_AUTH", "password"),
        ("ARCHLENS_RUN_BUDGET_USD", "-1"),
    ],
)
def test_invalid_environment_names_the_variable(
    monkeypatch: pytest.MonkeyPatch, var: str, value: str
) -> None:
    monkeypatch.setenv(var, value)
    with pytest.raises(ConfigError) as info:
        load_settings(env_file=None)
    assert info.value.field == var
    assert info.value.source == "environment"


# --- config/*.yaml ----------------------------------------------------------------------------


def test_shipped_config_files_load(config_dir: Path) -> None:
    config = load_config(settings_for(config_dir))
    assert config.models.roles.evaluator.deployment == "gpt-5-mini"
    assert config.models.roles.verifier.temperature == 0
    assert config.models.sessions.evaluator.max_tool_calls == 24
    assert config.pricing.deployments["gpt-5-mini"].output == 2.00
    assert config.tools.tools["semgrep"].rulesets == ["p/default"]


def test_deployment_override_from_environment(config_dir: Path) -> None:
    pricing = config_dir / "pricing.yaml"
    pricing.write_text(
        pricing.read_text() + "  my-model:\n    input: 1\n    cached_input: 1\n    output: 1\n"
    )
    config = load_config(settings_for(config_dir, model_evaluator="my-model"))
    assert config.models.roles.evaluator.deployment == "my-model"
    assert config.models.roles.evaluator.reasoning_effort == "low"  # other params kept
    assert config.models.roles.skeptic.deployment == "gpt-5-mini"  # other roles untouched


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"fixture drift: {old!r} not in {path.name}"
    path.write_text(text.replace(old, new, 1))


@pytest.mark.parametrize(
    ("file_name", "old", "new", "field"),
    [
        ("models.yaml", "  embed:\n    deployment: text-embedding-3-small\n", "", "roles.embed"),
        (
            "models.yaml",
            "max_tool_calls: 24",
            "max_tool_calls: lots",
            "sessions.evaluator.max_tool_calls",
        ),
        (
            "models.yaml",
            "reasoning_effort: low",
            "reasoning_effort: extreme",
            "roles.evaluator.reasoning_effort",
        ),
        ("models.yaml", "max_facts: 150", "max_fact: 150", "sessions.evaluator.max_fact"),
        ("pricing.yaml", "currency: USD", "currency: EUR", "currency"),
        ("pricing.yaml", "output: 2.00", "output: -2", "deployments.gpt-5-mini.output"),
        ("tools.yaml", "timeout_s: 120}", "timeout: 120}", "tools.gitleaks.timeout_s"),
    ],
)
def test_invalid_config_names_the_field(
    config_dir: Path, file_name: str, old: str, new: str, field: str
) -> None:
    _edit(config_dir / file_name, old, new)
    with pytest.raises(ConfigError) as info:
        load_config(settings_for(config_dir))
    assert info.value.field == field
    assert info.value.source.endswith(file_name)


def test_missing_price_for_a_used_deployment(config_dir: Path) -> None:
    with pytest.raises(ConfigError, match="no price") as info:
        load_config(settings_for(config_dir, model_verifier="unpriced-model"))
    assert info.value.field == "deployments.unpriced-model"


def test_missing_file(config_dir: Path) -> None:
    (config_dir / "tools.yaml").unlink()
    with pytest.raises(ConfigError, match="file not found") as info:
        load_config(settings_for(config_dir))
    assert info.value.source.endswith("tools.yaml")


def test_invalid_yaml(config_dir: Path) -> None:
    (config_dir / "pricing.yaml").write_text("deployments: [unclosed\n")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(settings_for(config_dir))
