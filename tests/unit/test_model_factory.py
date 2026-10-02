import pytest

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.llm.anthropic import AnthropicProvider
from mtg_deck_advisor.llm.factory import build_provider


def settings(**values: object) -> Settings:
    return Settings(_env_file=None, database_url="postgresql://x:y@127.0.0.1/db", **values)  # type: ignore[arg-type]


def test_the_anthropic_provider_is_built_from_settings() -> None:
    provider = build_provider(settings(model_provider="anthropic", anthropic_api_key="sk-ant-x"))

    assert isinstance(provider, AnthropicProvider)


def test_the_anthropic_provider_needs_a_key() -> None:
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        build_provider(settings(model_provider="anthropic"))


def test_the_ollama_provider_is_built_from_settings() -> None:
    from mtg_deck_advisor.llm.ollama import OllamaProvider

    provider = build_provider(settings(model_provider="ollama", model_name="qwen3:8b"))

    assert isinstance(provider, OllamaProvider)


def test_the_embedder_uses_the_configured_ollama_model() -> None:
    from mtg_deck_advisor.llm.factory import build_embedder

    embedder = build_embedder(settings(embedding_model="nomic-embed-text"))

    assert embedder.model == "nomic-embed-text"
