"""Application settings, read from the environment (SEC-1).

Every setting comes from an environment variable of the same name, or from a
local `.env` file during development. Nothing secret has a default, so a
missing secret fails at startup instead of silently using a placeholder.
"""

from functools import lru_cache
from pathlib import Path
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

    # Where the API listens. Localhost by default, so a development server is
    # never reachable from the network by accident; the container image sets
    # 0.0.0.0, and Docker's port mapping decides what is exposed.
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    # Where downloaded source files (Scryfall bulk data, the rules file) are
    # cached. Relative paths are relative to the working directory.
    cache_dir: Path = Path("data/cache")
    # Scryfall asks every API client for a User-Agent naming the application
    # and how to reach its maintainer.
    # The Comprehensive Rules text file, pinned to one dated edition so that
    # moving to a new edition is a deliberate change. Wizards links the
    # current file from https://magic.wizards.com/en/rules.
    rules_url: str = "https://media.wizards.com/2026/downloads/MagicCompRules%2020260925.txt"
    scryfall_user_agent: str = (
        "mtg-deck-advisor/0.1 (+https://github.com/Bacchetto/mtg-deck-advisor)"
    )


@lru_cache
def get_settings() -> Settings:
    """The process-wide settings, read once on first use."""
    return Settings()
