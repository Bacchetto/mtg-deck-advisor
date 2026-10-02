"""Real calls to the local Ollama: free, but need it running with the models pulled."""

import pytest
from pydantic import BaseModel

from mtg_deck_advisor.config import get_settings
from mtg_deck_advisor.llm.client import ModelClient
from mtg_deck_advisor.llm.factory import build_embedder
from mtg_deck_advisor.llm.ollama import OllamaProvider
from mtg_deck_advisor.llm.recording import MemoryRecorder
from mtg_deck_advisor.llm.types import Message, ModelRequest

pytestmark = pytest.mark.live


class Answer(BaseModel):
    card_name: str
    mana_value: int


def test_a_local_structured_call_succeeds() -> None:
    settings = get_settings()
    provider = OllamaProvider(
        base_url=settings.ollama_base_url, timeout_seconds=settings.ollama_timeout_seconds
    )
    client = ModelClient(provider, settings.ollama_chat_model, recorder=MemoryRecorder())

    result = client.generate_structured(
        ModelRequest(
            purpose="live_smoke_test",
            system="Answer questions about Magic: The Gathering cards in the requested format.",
            messages=(Message(role="user", content="What is the mana value of Sol Ring?"),),
            max_tokens=200,
        ),
        Answer,
    )

    assert result.value.mana_value == 1
    assert result.response.cost_usd == 0


def test_local_embeddings_have_a_consistent_dimension() -> None:
    embedder = build_embedder(get_settings())

    first, second = embedder.embed(["Sol Ring", "Destroy target creature."])

    assert len(first) == len(second) > 0
