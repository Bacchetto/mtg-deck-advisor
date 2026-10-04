from pathlib import Path

import pytest
from pydantic import ValidationError

from mtg_deck_advisor.config import Settings, get_settings

DATABASE_URL = "postgresql://user:hunter2@db.example:5432/mtg"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test from an environment with none of our settings in it."""
    for name in ("DATABASE_URL", "LOG_LEVEL", "LOG_FORMAT", "ENVIRONMENT"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()


def load() -> Settings:
    # _env_file=None: read the process environment only, never a developer's .env.
    return Settings(_env_file=None)


def test_loads_values_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("LOG_FORMAT", "console")
    monkeypatch.setenv("ENVIRONMENT", "test")

    settings = load()

    assert settings.database_url.get_secret_value() == DATABASE_URL
    assert settings.log_level == "DEBUG"
    assert settings.log_format == "console"
    assert settings.environment == "test"


def test_defaults_apply_when_only_the_database_url_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)

    settings = load()

    assert settings.log_level == "INFO"
    assert settings.log_format == "json"
    assert settings.environment == "development"


def test_missing_database_url_fails_with_a_clear_error() -> None:
    with pytest.raises(ValidationError) as excinfo:
        load()

    assert "database_url" in str(excinfo.value)


def test_database_url_is_never_shown_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)

    settings = load()

    assert "hunter2" not in repr(settings)
    assert "hunter2" not in str(settings)


def test_unknown_log_level_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("LOG_LEVEL", "LOUD")

    with pytest.raises(ValidationError) as excinfo:
        load()

    assert "log_level" in str(excinfo.value)


def test_get_settings_returns_one_shared_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)

    assert get_settings() is get_settings()


def test_ingestion_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.delenv("CACHE_DIR", raising=False)
    monkeypatch.delenv("SCRYFALL_USER_AGENT", raising=False)

    settings = load()

    assert settings.cache_dir == Path("data/cache")
    # Scryfall asks for a User-Agent that identifies the application.
    assert settings.scryfall_user_agent.startswith("mtg-deck-advisor/")


def test_the_rules_source_is_pinned_to_one_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.delenv("RULES_URL", raising=False)

    # A dated file, so updating the rules is a deliberate change of this setting.
    assert load().rules_url.endswith("MagicCompRules%2020260925.txt")


def test_model_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    for name in ("MODEL_PROVIDER", "MODEL_NAME", "MODEL_TIMEOUT_SECONDS", "RUN_COST_CAP_USD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    settings = load()

    assert settings.model_provider == "anthropic"
    assert settings.model_name == "claude-opus-5-5"
    assert settings.model_timeout_seconds == 60
    # Small by default: a run that needs more must ask for it.
    assert settings.run_cost_cap_usd == 1.0
    assert settings.anthropic_api_key is None


def test_the_anthropic_api_key_is_never_shown_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-value")

    settings = load()

    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "sk-ant-secret-value"
    assert "sk-ant-secret-value" not in repr(settings)


def test_an_empty_variable_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # .env.example ships "ANTHROPIC_API_KEY=" with no value: that means no key,
    # not a key that is an empty string.
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    assert load().anthropic_api_key is None


def test_role_tagging_uses_the_model_chosen_by_the_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # evals/reports/2026-10-02-role-tagging-comparison.md: best F1 and exact match.
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.delenv("ROLE_TAGGING_MODEL", raising=False)

    assert load().role_tagging_model == "claude-opus-5-5"


def test_retrieval_model_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    for name in ("EMBEDDING_MODEL", "EMBEDDING_DIMENSIONS"):
        monkeypatch.delenv(name, raising=False)

    settings = load()

    # Chosen on the retrieval dev set (#73): the 8b model, truncated to the
    # schema's 1,024 dimensions.
    assert settings.embedding_model == "qwen3-embedding:8b"
    assert settings.embedding_dimensions == 1024
