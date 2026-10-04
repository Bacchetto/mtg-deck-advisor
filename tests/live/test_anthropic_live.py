"""Real calls to the Anthropic API, a few cents in all; run only with --live."""

import pytest
from pydantic import BaseModel

from mtg_deck_advisor.config import get_settings
from mtg_deck_advisor.llm.client import ModelClient, ModelResponse
from mtg_deck_advisor.llm.factory import build_provider
from mtg_deck_advisor.llm.recording import MemoryRecorder
from mtg_deck_advisor.llm.types import Message, ModelRequest, ToolResult, ToolSpec

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


MANA_VALUE = ToolSpec(
    name="get_mana_value",
    description="Look up a Magic card's mana value by its exact name.",
    input_schema={
        "type": "object",
        "properties": {"card_name": {"type": "string"}},
        "required": ["card_name"],
    },
)


def test_a_real_tool_round_trip_keeps_thinking_and_uses_the_result() -> None:
    # Sonnet 5.5 is the agent's model (Milestone 5). Two calls; about $0.02.
    settings = get_settings()
    client = ModelClient(
        build_provider(settings), "claude-sonnet-5-5", recorder=MemoryRecorder(), cost_cap_usd=0.10
    )
    question = Message(
        role="user",
        content="Use the tool to look up Sol Ring's mana value, then tell me the number.",
    )

    def ask(*messages: Message) -> ModelResponse:
        return client.generate(
            ModelRequest(
                purpose="live_smoke_test",
                system="You answer questions about Magic cards. Use the tool to look up facts.",
                messages=messages,
                tools=(MANA_VALUE,),
                max_tokens=4000,
                effort="medium",
            )
        )

    first = ask(question)
    (call,) = first.tool_calls
    assert call.name == "get_mana_value" and "sol ring" in call.arguments["card_name"].lower()
    # A made-up value proves the answer came from the tool result, not memory.
    result = Message(role="user", tool_results=(ToolResult(tool_call_id=call.id, content="7"),))
    second = ask(question, first.as_message(), result)

    assert not second.tool_calls
    assert "7" in second.text
    for response in (first, second):
        print(
            f"\nlive call: {response.model}, {response.usage}, "
            f"${response.cost_usd:.5f}, {response.latency_ms:.0f} ms"
        )
