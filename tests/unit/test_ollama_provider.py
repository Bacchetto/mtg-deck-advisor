"""The Ollama provider and embedder, against a fake transport: no Ollama needed."""

import json
from typing import Any

import httpx2
import pytest

from mtg_deck_advisor.llm.errors import ModelCallError
from mtg_deck_advisor.llm.ollama import OllamaEmbedder, OllamaProvider
from mtg_deck_advisor.llm.types import (
    Message,
    ProviderRequest,
    ToolCall,
    ToolResult,
    ToolSpec,
)

BASE_URL = "http://ollama.test:11434"
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"roles": {"type": "array", "items": {"type": "string"}}},
    "required": ["roles"],
}


def chat_payload(**changes: Any) -> dict[str, Any]:
    """An /api/chat response body, as Ollama returns it."""
    payload: dict[str, Any] = {
        "model": "qwen3:8b",
        "created_at": "2026-10-02T18:00:00Z",
        "message": {"role": "assistant", "content": '{"roles": ["ramp"]}'},
        "done": True,
        "done_reason": "stop",
        "total_duration": 1_500_000_000,
        "prompt_eval_count": 85,
        "eval_count": 12,
    }
    payload.update(changes)
    return payload


class Server:
    def __init__(self, response: httpx2.Response | Exception) -> None:
        self.response = response
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def body(self) -> dict[str, Any]:
        result: dict[str, Any] = json.loads(self.requests[-1].content)
        return result


def http(server: Server) -> httpx2.Client:
    return httpx2.Client(transport=httpx2.MockTransport(server))


def provider_for(server: Server) -> OllamaProvider:
    return OllamaProvider(base_url=BASE_URL, timeout_seconds=30, http_client=http(server))


def request(**changes: Any) -> ProviderRequest:
    values: dict[str, Any] = {
        "purpose": "role_tagging",
        "system": "You tag Magic cards.",
        "messages": (Message(role="user", content="Tag Sol Ring."),),
        "max_tokens": 300,
        "output_schema": SCHEMA,
    }
    return ProviderRequest(**(values | changes))


# --- chat ------------------------------------------------------------------


def test_the_chat_request_carries_the_system_prompt_messages_and_limits() -> None:
    server = Server(httpx2.Response(200, json=chat_payload()))

    provider_for(server).complete(request(), "qwen3:8b")

    assert str(server.requests[-1].url) == f"{BASE_URL}/api/chat"
    body = server.body()
    assert body["model"] == "qwen3:8b"
    assert body["stream"] is False
    assert body["messages"] == [
        {"role": "system", "content": "You tag Magic cards."},
        {"role": "user", "content": "Tag Sol Ring."},
    ]
    assert body["options"]["num_predict"] == 300
    # Deterministic output for tagging and evals.
    assert body["options"]["temperature"] == 0


def test_structured_output_sends_the_json_schema_as_the_format() -> None:
    server = Server(httpx2.Response(200, json=chat_payload()))

    provider_for(server).complete(request(), "qwen3:8b")

    assert server.body()["format"] == SCHEMA


def test_thinking_is_off_unless_high_effort_is_asked_for() -> None:
    server = Server(httpx2.Response(200, json=chat_payload()))
    provider = provider_for(server)

    provider.complete(request(effort=None), "qwen3:8b")
    assert server.body()["think"] is False
    provider.complete(request(effort="low"), "qwen3:8b")
    assert server.body()["think"] is False
    provider.complete(request(effort="high"), "qwen3:8b")
    assert server.body()["think"] is True


def test_the_reply_maps_text_usage_and_stop_reason() -> None:
    server = Server(httpx2.Response(200, json=chat_payload()))

    reply = provider_for(server).complete(request(), "qwen3:8b")

    assert reply.text == '{"roles": ["ramp"]}'
    assert reply.stop_reason == "end_turn"
    assert (reply.usage.input_tokens, reply.usage.output_tokens) == (85, 12)
    assert reply.model == "qwen3:8b"


def test_running_out_of_output_tokens_is_reported_as_max_tokens() -> None:
    server = Server(httpx2.Response(200, json=chat_payload(done_reason="length")))

    assert provider_for(server).complete(request(), "qwen3:8b").stop_reason == "max_tokens"


def test_a_local_model_costs_nothing() -> None:
    provider = provider_for(Server(httpx2.Response(200, json=chat_payload())))

    assert provider.bills is False
    assert provider.name == "ollama"


def test_ollama_not_running_gives_an_actionable_error() -> None:
    server = Server(httpx2.ConnectError("connection refused"))

    with pytest.raises(ModelCallError, match="Is Ollama running"):
        provider_for(server).complete(request(), "qwen3:8b")


def test_a_model_that_is_not_pulled_gives_an_actionable_error() -> None:
    server = Server(httpx2.Response(404, json={"error": "model 'qwen3:8b' not found"}))

    with pytest.raises(ModelCallError, match="ollama pull qwen3:8b"):
        provider_for(server).complete(request(), "qwen3:8b")


# --- tool calling (#152) -------------------------------------------------------------------
# The payloads are Ollama 0.40's own, recorded from qwen3:14b on 2026-10-13.

GET_CARD = ToolSpec(
    name="get_card",
    description="Look up one card by name.",
    input_schema={
        "type": "object",
        "properties": {"name": {"type": "string", "description": "The card name."}},
        "required": ["name"],
    },
)
TOOL_CALL_REPLY: dict[str, Any] = {
    "model": "qwen3:14b",
    "created_at": "2026-10-13T14:46:28.946296Z",
    "message": {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_c8nzindu",
                "function": {"index": 0, "name": "get_card", "arguments": {"name": "Sol Ring"}},
            }
        ],
    },
    "done": True,
    "done_reason": "stop",
    "prompt_eval_count": 160,
    "eval_count": 21,
}


def test_tools_are_sent_as_function_definitions() -> None:
    server = Server(httpx2.Response(200, json=TOOL_CALL_REPLY))

    provider_for(server).complete(request(tools=(GET_CARD,)), "qwen3:14b")

    assert server.body()["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "get_card",
                "description": "Look up one card by name.",
                "parameters": GET_CARD.input_schema,
            },
        }
    ]


def test_a_tool_call_reply_becomes_the_projects_tool_calls() -> None:
    server = Server(httpx2.Response(200, json=TOOL_CALL_REPLY))

    reply = provider_for(server).complete(request(tools=(GET_CARD,)), "qwen3:14b")

    assert reply.stop_reason == "tool_use"
    assert reply.tool_calls == (
        ToolCall(id="call_c8nzindu", name="get_card", arguments={"name": "Sol Ring"}),
    )
    assert (reply.usage.input_tokens, reply.usage.output_tokens) == (160, 21)


def test_a_tool_call_without_an_id_or_with_text_arguments_still_maps() -> None:
    # Older Ollama versions send no id, and some models send arguments as a JSON string.
    payload = json.loads(json.dumps(TOOL_CALL_REPLY))
    del payload["message"]["tool_calls"][0]["id"]
    payload["message"]["tool_calls"][0]["function"]["arguments"] = '{"name": "Sol Ring"}'
    server = Server(httpx2.Response(200, json=payload))

    (call,) = provider_for(server).complete(request(tools=(GET_CARD,)), "qwen3:14b").tool_calls

    assert call.id == "call_1" and call.arguments == {"name": "Sol Ring"}


def test_tool_calls_and_results_go_back_as_assistant_and_tool_messages() -> None:
    server = Server(httpx2.Response(200, json=chat_payload()))
    asked = ToolCall(id="call_c8nzindu", name="get_card", arguments={"name": "Sol Ring"})
    messages = (
        Message(role="user", content="Look up the card Sol Ring."),
        Message(role="assistant", tool_calls=(asked,)),
        Message(
            role="user",
            tool_results=(
                ToolResult(tool_call_id="call_c8nzindu", content="Sol Ring | {1} | Artifact"),
            ),
        ),
    )

    provider_for(server).complete(request(messages=messages, tools=(GET_CARD,)), "qwen3:14b")

    sent = server.body()["messages"]
    assert sent[2] == {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_c8nzindu",
                "function": {"name": "get_card", "arguments": {"name": "Sol Ring"}},
            }
        ],
    }
    assert sent[3] == {
        "role": "tool",
        "tool_call_id": "call_c8nzindu",
        "tool_name": "get_card",
        "content": "Sol Ring | {1} | Artifact",
    }


def test_nested_argument_schemas_are_inlined() -> None:
    # Pydantic puts nested models under $defs; a local model may not follow a $ref.
    schema = {
        "type": "object",
        "properties": {"cards": {"type": "array", "items": {"$ref": "#/$defs/CardCount"}}},
        "$defs": {
            "CardCount": {"type": "object", "properties": {"name": {"type": "string"}}},
        },
    }
    server = Server(httpx2.Response(200, json=TOOL_CALL_REPLY))
    tool = ToolSpec(name="propose_changes", description="d", input_schema=schema)

    provider_for(server).complete(request(tools=(tool,)), "qwen3:14b")

    sent = server.body()["tools"][0]["function"]["parameters"]
    assert sent["properties"]["cards"]["items"] == schema["$defs"]["CardCount"]
    assert "$defs" not in sent and "$ref" not in json.dumps(sent)


def test_an_agent_request_asks_for_a_context_that_fits_it() -> None:
    # Ollama's default context is a few thousand tokens and it cuts a longer
    # prompt silently: an agent turn with a decklist and tool results is longer.
    server = Server(httpx2.Response(200, json=TOOL_CALL_REPLY))
    provider = OllamaProvider(
        base_url=BASE_URL, timeout_seconds=30, http_client=http(server), agent_num_ctx=16384
    )

    provider.complete(request(tools=(GET_CARD,)), "qwen3:14b")
    with_tools = server.body()["options"]
    provider.complete(request(), "qwen3:8b")
    without = server.body()["options"]

    assert with_tools["num_ctx"] == 16384
    assert "num_ctx" not in without  # tagging and reranking keep their small footprint


# --- embeddings ------------------------------------------------------------


def test_embeddings_are_requested_in_one_batch_and_returned_in_order() -> None:
    server = Server(
        httpx2.Response(
            200, json={"model": "nomic-embed-text", "embeddings": [[0.1, 0.2], [0.3, 0.4]]}
        )
    )
    embedder = OllamaEmbedder(
        base_url=BASE_URL, model="nomic-embed-text", timeout_seconds=30, http_client=http(server)
    )

    vectors = embedder.embed(["Sol Ring", "Lightning Bolt"])

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert str(server.requests[-1].url) == f"{BASE_URL}/api/embed"
    assert server.body() == {"model": "nomic-embed-text", "input": ["Sol Ring", "Lightning Bolt"]}


def test_large_inputs_are_split_into_batches() -> None:
    def reply(request: httpx2.Request) -> httpx2.Response:
        inputs = json.loads(request.content)["input"]
        return httpx2.Response(200, json={"embeddings": [[float(len(t))] for t in inputs]})

    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return reply(request)

    embedder = OllamaEmbedder(
        base_url=BASE_URL,
        model="nomic-embed-text",
        timeout_seconds=30,
        batch_size=2,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )

    vectors = embedder.embed(["a", "bb", "ccc", "dddd", "eeeee"])

    assert vectors == [[1.0], [2.0], [3.0], [4.0], [5.0]]
    assert len(requests) == 3


def test_no_texts_means_no_request() -> None:
    server = Server(httpx2.Response(500))
    embedder = OllamaEmbedder(
        base_url=BASE_URL, model="nomic-embed-text", timeout_seconds=30, http_client=http(server)
    )

    assert embedder.embed([]) == []
    assert server.requests == []


def test_a_mismatched_embedding_count_is_an_error() -> None:
    server = Server(httpx2.Response(200, json={"embeddings": [[0.1, 0.2]]}))
    embedder = OllamaEmbedder(
        base_url=BASE_URL, model="nomic-embed-text", timeout_seconds=30, http_client=http(server)
    )

    with pytest.raises(ModelCallError, match="2 texts"):
        embedder.embed(["Sol Ring", "Lightning Bolt"])


def test_vectors_are_truncated_to_the_configured_size_and_renormalised() -> None:
    server = Server(httpx2.Response(200, json={"embeddings": [[3.0, 4.0, 12.0]]}))
    embedder = OllamaEmbedder(
        base_url=BASE_URL,
        model="qwen3-embedding:8b",
        timeout_seconds=30,
        dimensions=2,
        http_client=http(server),
    )

    (vector,) = embedder.embed(["Sol Ring"])

    assert vector == pytest.approx([0.6, 0.8])


def test_vectors_shorter_than_the_configured_size_are_an_error() -> None:
    server = Server(httpx2.Response(200, json={"embeddings": [[0.6, 0.8]]}))
    embedder = OllamaEmbedder(
        base_url=BASE_URL,
        model="nomic-embed-text",
        timeout_seconds=30,
        dimensions=1024,
        http_client=http(server),
    )

    with pytest.raises(ModelCallError, match="1024"):
        embedder.embed(["Sol Ring"])


PORTS_EXHAUSTED = (
    '{"error":"Post \\"http://127.0.0.1:57652/tokenize\\": dial tcp 127.0.0.1:57652: bind: '
    "An operation on a socket could not be performed because the system lacked sufficient "
    'buffer space or because a queue was full."}'
)


def test_running_out_of_network_ports_is_waited_out_and_retried() -> None:
    replies = [
        httpx2.Response(400, text=PORTS_EXHAUSTED),
        httpx2.Response(200, json={"embeddings": [[0.6, 0.8]]}),
    ]
    waits: list[float] = []
    embedder = OllamaEmbedder(
        base_url=BASE_URL,
        model="qwen3-embedding:8b",
        timeout_seconds=30,
        http_client=httpx2.Client(transport=httpx2.MockTransport(lambda _: replies.pop(0))),
        sleep=waits.append,
    )

    assert embedder.embed(["Sol Ring"]) == [[0.6, 0.8]]
    assert len(waits) == 1 and waits[0] >= 30


def test_other_errors_are_not_retried() -> None:
    server = Server(httpx2.Response(400, text='{"error":"input too long"}'))
    waits: list[float] = []
    embedder = OllamaEmbedder(
        base_url=BASE_URL,
        model="qwen3-embedding:8b",
        timeout_seconds=30,
        http_client=http(server),
        sleep=waits.append,
    )

    with pytest.raises(ModelCallError, match="input too long"):
        embedder.embed(["Sol Ring"])
    assert waits == [] and len(server.requests) == 1


def test_port_exhaustion_that_persists_is_reported() -> None:
    server = Server(httpx2.Response(400, text=PORTS_EXHAUSTED))
    embedder = OllamaEmbedder(
        base_url=BASE_URL,
        model="qwen3-embedding:8b",
        timeout_seconds=30,
        http_client=http(server),
        sleep=lambda _: None,
    )

    with pytest.raises(ModelCallError, match="network ports"):
        embedder.embed(["Sol Ring"])
    assert len(server.requests) > 1
