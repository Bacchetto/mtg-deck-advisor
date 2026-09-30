"""Application settings, read from the environment (SEC-1).

Every setting comes from an environment variable of the same name, or from a
local `.env` file during development. Nothing secret has a default, so a
missing secret fails at startup instead of silently using a placeholder.
"""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # .env also holds values for other tools (such as Docker Compose), so
        # unknown keys are ignored rather than rejected.
        extra="ignore",
    )

    # SecretStr keeps the password out of repr() and str(), so a settings
    # object that ends up in a log line or a traceback does not leak it.
    database_url: SecretStr

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # JSON for containers and anything that ships logs; "console" for a
    # readable local terminal.
    log_format: Literal["json", "console"] = "json"
    environment: Literal["development", "test", "production"] = "development"


@lru_cache
def get_settings() -> Settings:
    """The process-wide settings, read once on first use."""
    return Settings()
