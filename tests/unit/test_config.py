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
    return Settings(_env_file=None)  # type: ignore[call-arg]


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
