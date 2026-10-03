"""Building the configured provider and model client from settings (MOD-4).

Which provider serves model calls is configuration (`MODEL_PROVIDER`), so
switching it needs no code change.
"""

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.llm.anthropic import AnthropicProvider
from mtg_deck_advisor.llm.client import ModelClient
from mtg_deck_advisor.llm.ollama import Embedder, OllamaEmbedder, OllamaProvider
from mtg_deck_advisor.llm.recording import DatabaseRecorder
from mtg_deck_advisor.llm.replay import RecordingProvider, ReplayProvider
from mtg_deck_advisor.llm.types import Provider


def build_provider(settings: Settings) -> Provider:
    """The configured provider, wrapped to record its responses if RECORD_RESPONSES is set."""
    if settings.model_provider == "replay":
        if settings.record_responses:
            raise ValueError(
                "RECORD_RESPONSES needs a real MODEL_PROVIDER (anthropic or ollama) to record from"
            )
        return ReplayProvider(settings.replay_dir)

    provider: Provider
    if settings.model_provider == "anthropic":
        key = settings.anthropic_api_key
        provider = AnthropicProvider(
            api_key=key.get_secret_value() if key else None,
            timeout_seconds=settings.model_timeout_seconds,
        )
    else:
        provider = OllamaProvider(
            base_url=settings.ollama_base_url, timeout_seconds=settings.ollama_timeout_seconds
        )
    if settings.record_responses:
        return RecordingProvider(provider, settings.replay_dir)
    return provider


def build_embedder(settings: Settings) -> Embedder:
    """The embedding model used for retrieval: local, through Ollama."""
    return OllamaEmbedder(
        base_url=settings.ollama_base_url,
        model=settings.embedding_model,
        timeout_seconds=settings.ollama_timeout_seconds,
        dimensions=settings.embedding_dimensions,
    )


def build_model_client(settings: Settings) -> ModelClient:
    """The model client for one run: the configured provider, recorded to the database."""
    return ModelClient(
        build_provider(settings),
        settings.model_name,
        recorder=DatabaseRecorder(settings),
        cost_cap_usd=settings.run_cost_cap_usd,
    )
