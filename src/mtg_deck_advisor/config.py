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
        # "NAME=" with no value means unset, so an empty placeholder such as
        # ANTHROPIC_API_KEY= is no key, not a blank one.
        env_ignore_empty=True,
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

    # Model calls (ADR 0008). The provider and model are configuration, so
    # switching provider is a settings change with no code change (MOD-4).
    model_provider: Literal["anthropic", "ollama", "replay"] = "anthropic"
    model_name: str = "claude-opus-5-5"
    model_timeout_seconds: float = 60
    # The most one run (a process, or one request through the API) may spend
    # on model calls. Small by default: a run that needs more must ask for it.
    run_cost_cap_usd: float = 1.0
    # Needed only for the anthropic provider. Never logged or shown.
    anthropic_api_key: SecretStr | None = None

    # The model that tags card roles, chosen by comparison against the
    # owner-reviewed gold sample (evals/reports/2026-10-02-role-tagging-comparison.md):
    # best F1 (0.93) and exact match (0.90), about $1.19 per 1,000 newly seen cards.
    role_tagging_model: str = "claude-opus-5-5"

    # Recorded model responses (llm.replay). MODEL_PROVIDER=replay serves them
    # with no network or key; RECORD_RESPONSES=true saves every response a real
    # provider returns. The directory is committed, so a clean clone can replay.
    replay_dir: Path = Path("recordings")
    record_responses: bool = False

    # The local model server (ADR 0009). Ollama runs natively on the host so it
    # can use the GPU; containers reach it as host.docker.internal.
    ollama_base_url: str = "http://127.0.0.1:11434"
    # Generous: loading a large model into memory the first time takes a while.
    ollama_timeout_seconds: float = 300
    # The local chat model, when MODEL_PROVIDER is ollama or for comparisons.
    ollama_chat_model: str = "qwen3:14b"
    # The embedding model for retrieval, chosen on the retrieval dev set (#73),
    # and the size its vectors are cut to: the schema's vector(1024).
    embedding_model: str = "qwen3-embedding:8b"
    embedding_dimensions: int = 1024

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
