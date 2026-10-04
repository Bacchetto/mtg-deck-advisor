"""Native tool calling in the model layer (AGT-1): types, the Anthropic mapping, client, replay."""

import json
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic import ValidationError

from mtg_deck_advisor.llm.anthropic import AnthropicProvider
from mtg_deck_advisor.llm.client import ModelClient
from mtg_deck_advisor.llm.fake import FakeProvider, tool_call_reply
from mtg_deck_advisor.llm.recording import MemoryRecorder
from mtg_deck_advisor.llm.replay import RecordingProvider, ReplayProvider, request_key
from mtg_deck_advisor.llm.types import (
    Message,
    ModelRequest,
    ProviderRequest,
    ToolCall,
    ToolResult,
    ToolSpec,
)

SEARCH = ToolSpec(
    name="search_pool",
    description="Search the user's card pool.",
    input_schema={
        "type": "object",
        "properties": {"query": {"type": "string"}, "k": {"type": "integer", "minimum": 1}},
        "required": ["query"],
    },
)


# --- types -----------------------------------------------------------------


def test_only_assistant_turns_carry_tool_calls_and_only_user_turns_carry_results() -> None:
    call = ToolCall(id="toolu_1", name="search_pool", arguments={"query": "ramp"})
    result = ToolResult(tool_call_id="toolu_1", content="Sol Ring")

    Message(role="assistant", tool_calls=(call,))
    Message(role="user", tool_results=(result,))
    with pytest.raises(ValidationError, match="assistant"):
        Message(role="user", tool_calls=(call,))
    with pytest.raises(ValidationError, match="user"):
        Message(role="assistant", tool_results=(result,))


def test_a_text_only_request_keeps_its_replay_key() -> None:
    # Existing recordings are keyed by this hash: adding tool fields mustn't change it.
    request = ProviderRequest(
        purpose="x",
        system="You tag cards.",
        messages=(Message(role="user", content="Tag Sol Ring."),),
        max_tokens=500,
        effort="low",
        output_schema={"type": "object"},
    )

    assert request_key(request, "claude-sonnet-5-5") == (
        "851c9aa039856b373ce888869755d5df4961b56dfe43adf47c0de732b37db8d4"
    )


def test_tools_and_tool_turns_change_the_replay_key() -> None:
    base = ProviderRequest(purpose="x", system="s", messages=(Message(role="user", content="q"),))
    with_tools = base.model_copy(update={"tools": (SEARCH,)})

    assert request_key(base, "m") != request_key(with_tools, "m")


# --- the Anthropic provider -------------------------------------------------


THINKING = {"type": "thinking", "thinking": "", "signature": "sig-abc"}
TOOL_USE = {
    "type": "tool_use",
    "id": "toolu_01",
    "name": "search_pool",
    "input": {"query": "cheap ramp", "k": 5},
}


def payload(content: list[dict[str, Any]], stop_reason: str) -> dict[str, Any]:
    return {
        "id": "msg_01",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 200, "output_tokens": 30},
    }


class Server:
    def __init__(self, *bodies: dict[str, Any]) -> None:
        self.bodies = list(bodies)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        return httpx2.Response(200, json=self.bodies.pop(0))


def provider(server: Server) -> AnthropicProvider:
    return AnthropicProvider(
        api_key="sk-ant-test",
        timeout_seconds=30,
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(server)),
    )


def request(*messages: Message) -> ProviderRequest:
    return ProviderRequest(
        purpose="agent",
        system="You build decks.",
        messages=messages or (Message(role="user", content="Draft a deck."),),
        tools=(SEARCH,),
        max_tokens=16000,
        effort="medium",
    )


def test_tools_are_sent_strict_and_the_model_chooses_when_to_call_them() -> None:
    server = Server(payload([{"type": "text", "text": "Done."}], "end_turn"))

    provider(server).complete(request(), "claude-sonnet-5-5")

    (tool,) = server.requests[0]["tools"]
    assert tool["name"] == "search_pool" and tool["strict"] is True
    assert tool["input_schema"]["additionalProperties"] is False
    assert "minimum" not in tool["input_schema"]["properties"]["k"]  # unsupported in strict
    assert "tool_choice" not in server.requests[0]  # auto: forced tool use is a 400 here


def test_a_tool_use_reply_gives_tool_calls_and_keeps_the_raw_turn() -> None:
    server = Server(
        payload([THINKING, {"type": "text", "text": "Searching."}, TOOL_USE], "tool_use")
    )

    response = provider(server).complete(request(), "claude-sonnet-5-5")

    assert response.stop_reason == "tool_use"
    assert response.text == "Searching."
    assert response.tool_calls == (
        ToolCall(id="toolu_01", name="search_pool", arguments={"query": "cheap ramp", "k": 5}),
    )
    assert response.provider_content is not None
    assert [block["type"] for block in response.provider_content] == [
        "thinking",
        "text",
        "tool_use",
    ]


def test_the_assistant_turn_goes_back_unchanged_and_results_in_one_user_turn() -> None:
    first = Server(payload([THINKING, TOOL_USE], "tool_use"))
    reply = provider(first).complete(request(), "claude-sonnet-5-5")
    assistant = Message(
        role="assistant",
        content=reply.text,
        tool_calls=reply.tool_calls,
        provider_content=reply.provider_content,
    )
    results = Message(
        role="user",
        tool_results=(
            ToolResult(tool_call_id="toolu_01", content="1. Sol Ring"),
            ToolResult(tool_call_id="toolu_02", content="unknown tool", is_error=True),
        ),
    )
    second = Server(payload([{"type": "text", "text": "Done."}], "end_turn"))

    provider(second).complete(
        request(Message(role="user", content="Draft a deck."), assistant, results),
        "claude-sonnet-5-5",
    )

    sent = second.requests[0]["messages"]
    assert sent[1] == {"role": "assistant", "content": [THINKING, TOOL_USE]}
    assert sent[2] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "toolu_01", "content": "1. Sol Ring"},
            {
                "type": "tool_result",
                "tool_use_id": "toolu_02",
                "content": "unknown tool",
                "is_error": True,
            },
        ],
    }


def test_an_assistant_turn_from_elsewhere_is_rebuilt_from_its_text_and_calls() -> None:
    server = Server(payload([{"type": "text", "text": "Done."}], "end_turn"))
    assistant = Message(
        role="assistant",
        content="Let me look.",
        tool_calls=(ToolCall(id="call_1", name="search_pool", arguments={"query": "ramp"}),),
    )

    provider(server).complete(
        request(
            Message(role="user", content="Draft."),
            assistant,
            Message(role="user", tool_results=(ToolResult(tool_call_id="call_1", content="x"),)),
        ),
        "claude-sonnet-5-5",
    )

    assert server.requests[0]["messages"][1]["content"] == [
        {"type": "text", "text": "Let me look."},
        {"type": "tool_use", "id": "call_1", "name": "search_pool", "input": {"query": "ramp"}},
    ]


def test_plain_text_turns_are_sent_as_before() -> None:
    server = Server(payload([{"type": "text", "text": "Hi."}], "end_turn"))

    provider(server).complete(
        ProviderRequest(purpose="x", system="s", messages=(Message(role="user", content="q"),)),
        "claude-sonnet-5-5",
    )

    assert server.requests[0]["messages"] == [{"role": "user", "content": "q"}]
    assert "tools" not in server.requests[0]


# --- the client, the fake and replay ---------------------------------------


def test_the_client_returns_tool_calls_and_records_the_call() -> None:
    call = ToolCall(id="t1", name="search_pool", arguments={"query": "ramp"})
    recorder = MemoryRecorder()
    client = ModelClient(
        FakeProvider(tool_call_reply(call, text="Looking.")), "m", recorder=recorder
    )

    response = client.generate(
        ModelRequest(
            purpose="agent",
            system="s",
            messages=(Message(role="user", content="q"),),
            tools=(SEARCH,),
        )
    )

    assert response.tool_calls == (call,)
    assert response.text == "Looking."
    assert response.as_message() == Message(
        role="assistant", content="Looking.", tool_calls=(call,), provider_content=None
    )
    (record,) = recorder.records
    assert record.outcome == "ok"
    # A turn with tool calls is recorded as JSON, so the call can be reconstructed.
    assert record.response_text is not None
    assert json.loads(record.response_text) == {
        "text": "Looking.",
        "tool_calls": [{"id": "t1", "name": "search_pool", "arguments": {"query": "ramp"}}],
    }


def test_a_tool_turn_survives_record_and_replay(tmp_path: Path) -> None:
    call = ToolCall(id="t1", name="search_pool", arguments={"query": "ramp"})
    req = request()
    recording = RecordingProvider(FakeProvider(tool_call_reply(call)), tmp_path)
    recorded = recording.complete(req, "claude-sonnet-5-5")

    replayed = ReplayProvider(tmp_path).complete(req, "claude-sonnet-5-5")

    assert replayed.tool_calls == recorded.tool_calls == (call,)
