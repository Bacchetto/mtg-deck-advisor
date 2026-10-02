"""One real call to the Anthropic API. Costs well under one cent; runs only with --live."""

import pytest
from pydantic import BaseModel

from mtg_deck_advisor.config import get_settings
from mtg_deck_advisor.llm.client import ModelClient
from mtg_deck_advisor.llm.factory import build_provider
from mtg_deck_advisor.llm.recording import MemoryRecorder
from mtg_deck_advisor.llm.types import Message, ModelRequest

pytestmark = pytest.mark.live


class Answer(BaseModel):
    card_name: str
    mana_value: int


def test_a_real_structured_call_succeeds_within_a_tiny_budget() -> None:
    settings = get_settings()
    recorder = MemoryRecorder()
    client = ModelClient(
        build_provider(settings), settings.model_name, recorder=recorder, cost_cap_usd=0.05
    )

    result = client.generate_structured(
        ModelRequest(
            purpose="live_smoke_test",
            system="Answer questions about Magic: The Gathering cards in the requested format.",
            messages=(Message(role="user", content="What is the mana value of Sol Ring?"),),
            max_tokens=2000,
            effort="low",
        ),
        Answer,
    )

    assert result.value.mana_value == 1
    assert result.response.cost_usd < 0.05
    print(
        f"\nlive call: {result.response.model}, {result.response.usage}, "
        f"${result.response.cost_usd:.5f}, {result.response.latency_ms:.0f} ms"
    )
