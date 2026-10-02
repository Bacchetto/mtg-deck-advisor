"""The Ollama provider and embedder, against a fake transport: no Ollama needed."""

import json
from typing import Any

import httpx2
import pytest

from mtg_deck_advisor.llm.errors import ModelCallError
from mtg_deck_advisor.llm.ollama import OllamaEmbedder, OllamaProvider
from mtg_deck_advisor.llm.types import Message, ProviderRequest

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
