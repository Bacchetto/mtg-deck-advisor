"""The Anthropic provider, driving the real SDK against a fake transport: no network, no cost."""

import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from mtg_deck_advisor.llm.anthropic import AnthropicProvider, strict_schema
from mtg_deck_advisor.llm.errors import ProviderAuthError, SpendLimitError
from mtg_deck_advisor.llm.types import Message, ProviderRequest

ROLES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "title": "Roles",
    "properties": {
        "roles": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "reason": {"type": "string", "maxLength": 200},
        "detail": {
            "type": "object",
            "properties": {"confidence": {"type": "number", "minimum": 0, "maximum": 1}},
        },
    },
    "required": ["roles", "reason"],
}


def message_payload(**changes: Any) -> dict[str, Any]:
    """A Messages API response body, as the API returns it."""
    payload: dict[str, Any] = {
        "id": "msg_01",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "text", "text": '{"roles": ["ramp"], "reason": "Adds mana."}'},
        ],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 120,
            "output_tokens": 45,
            "cache_read_input_tokens": 300,
            "cache_creation_input_tokens": 0,
        },
    }
    payload.update(changes)
    return payload


def error_payload(status: int, message: str, error_type: str, **details: str) -> httpx2.Response:
    error: dict[str, Any] = {"type": error_type, "message": message}
    if details:
        error["details"] = details
    return httpx2.Response(status, json={"type": "error", "error": error})


class Server:
    """A fake Messages API that records each request it receives."""

    def __init__(self, *responses: httpx2.Response | Callable[[], httpx2.Response]) -> None:
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return response() if callable(response) else response

    def body(self, index: int = -1) -> dict[str, Any]:
        result: dict[str, Any] = json.loads(self.requests[index].content)
        return result


def provider_for(server: Server, *, max_retries: int = 0) -> AnthropicProvider:
    return AnthropicProvider(
        api_key="sk-ant-test",
        timeout_seconds=30,
        max_retries=max_retries,
        http_client=httpx2.Client(transport=httpx2.MockTransport(server)),
    )


def request(**changes: Any) -> ProviderRequest:
    values: dict[str, Any] = {
        "purpose": "role_tagging",
        "system": "You tag Magic cards.",
        "messages": (Message(role="user", content="Tag Sol Ring."),),
        "max_tokens": 500,
        "effort": "low",
        "output_schema": ROLES_SCHEMA,
    }
    return ProviderRequest(**(values | changes))


# --- the request sent ------------------------------------------------------


def test_the_request_carries_the_prompt_limits_and_a_cached_system_prompt() -> None:
    server = Server(httpx2.Response(200, json=message_payload()))

    provider_for(server).complete(request(), "claude-opus-5-5")

    body = server.body()
    assert body["model"] == "claude-opus-5-5"
    assert body["max_tokens"] == 500
    assert body["messages"] == [{"role": "user", "content": "Tag Sol Ring."}]
    # The system prompt is the stable prefix, so it is marked for prompt caching.
    assert body["system"] == [
        {"type": "text", "text": "You tag Magic cards.", "cache_control": {"type": "ephemeral"}}
    ]
    assert server.requests[-1].headers["x-api-key"] == "sk-ant-test"


def test_structured_output_sends_a_strict_json_schema() -> None:
    server = Server(httpx2.Response(200, json=message_payload()))

    provider_for(server).complete(request(), "claude-opus-5-5")

    output_format = server.body()["output_config"]["format"]
    assert output_format == {"type": "json_schema", "schema": strict_schema(ROLES_SCHEMA)}


def test_strict_schema_closes_every_object_and_drops_unsupported_constraints() -> None:
    schema = strict_schema(ROLES_SCHEMA)

    assert schema["additionalProperties"] is False
    assert schema["properties"]["detail"]["additionalProperties"] is False
    assert "minItems" not in schema["properties"]["roles"]
    assert "maxLength" not in schema["properties"]["reason"]
    assert "minimum" not in schema["properties"]["detail"]["properties"]["confidence"]
    assert ROLES_SCHEMA["properties"]["reason"]["maxLength"] == 200  # the input is untouched


def test_a_plain_text_request_has_no_output_format() -> None:
    server = Server(httpx2.Response(200, json=message_payload()))

    provider_for(server).complete(request(output_schema=None), "claude-opus-5-5")

    assert "format" not in server.body().get("output_config", {})


def test_effort_is_sent_to_models_that_support_it() -> None:
    server = Server(httpx2.Response(200, json=message_payload()))

    provider_for(server).complete(request(effort="low"), "claude-opus-5-5")

    assert server.body()["output_config"]["effort"] == "low"


def test_effort_is_left_out_for_models_that_reject_it() -> None:
    server = Server(httpx2.Response(200, json=message_payload(model="claude-haiku-4-5")))

    provider_for(server).complete(request(effort="low"), "claude-haiku-4-5")

    assert "effort" not in server.body().get("output_config", {})


def test_opus_and_sonnet_5_5_requests_opt_into_default_refusal_fallbacks() -> None:
    server = Server(httpx2.Response(200, json=message_payload()))

    provider_for(server).complete(request(), "claude-sonnet-5-5")

    assert server.body()["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in server.requests[-1].headers["anthropic-beta"]


def test_other_models_do_not_send_fallbacks() -> None:
    server = Server(httpx2.Response(200, json=message_payload(model="claude-haiku-4-5")))

    provider_for(server).complete(request(), "claude-haiku-4-5")

    assert "fallbacks" not in server.body()
    assert "anthropic-beta" not in server.requests[-1].headers


# --- the response read -----------------------------------------------------


def test_the_reply_is_the_text_blocks_with_usage_and_the_model_that_answered() -> None:
    server = Server(httpx2.Response(200, json=message_payload(model="claude-opus-5")))

    reply = provider_for(server).complete(request(), "claude-opus-5-5")

    assert reply.text == '{"roles": ["ramp"], "reason": "Adds mana."}'
    assert reply.stop_reason == "end_turn"
    assert reply.model == "claude-opus-5"  # a fallback model answered
    assert reply.usage.input_tokens == 120
    assert reply.usage.output_tokens == 45
    assert reply.usage.cache_read_input_tokens == 300


def test_a_refusal_is_passed_on_as_its_stop_reason() -> None:
    refused = message_payload(content=[], stop_reason="refusal")
    server = Server(httpx2.Response(200, json=refused))

    reply = provider_for(server).complete(request(), "claude-opus-5-5")

    assert (reply.stop_reason, reply.text) == ("refusal", "")


# --- errors ----------------------------------------------------------------


def test_server_errors_are_retried_by_the_sdk() -> None:
    server = Server(
        error_payload(500, "Internal error", "api_error"),
        httpx2.Response(200, json=message_payload()),
    )

    reply = provider_for(server, max_retries=2).complete(request(), "claude-opus-5-5")

    assert reply.stop_reason == "end_turn"
    assert len(server.requests) == 2


@pytest.mark.parametrize(
    "response",
    [
        error_payload(
            400,
            "You have reached your specified API usage limits. You will regain access on "
            "2026-11-01 at 00:00 UTC.",
            "invalid_request_error",
        ),
        error_payload(
            400,
            "Your credit balance is too low to access the Anthropic API.",
            "invalid_request_error",
        ),
        error_payload(
            429,
            "You have reached your API usage limits.",
            "rate_limit_error",
            error_code="enforced_spend_limit_reached",
        ),
    ],
    ids=["console-spend-limit", "credit-balance", "tier-spend-cap"],
)
def test_spending_limits_raise_a_clear_error(response: httpx2.Response) -> None:
    server = Server(response)

    with pytest.raises(SpendLimitError):
        provider_for(server).complete(request(), "claude-opus-5-5")


def test_a_rejected_api_key_raises_a_clear_error() -> None:
    server = Server(error_payload(401, "invalid x-api-key", "authentication_error"))

    with pytest.raises(ProviderAuthError, match="ANTHROPIC_API_KEY"):
        provider_for(server).complete(request(), "claude-opus-5-5")


def test_a_missing_api_key_is_rejected_when_the_provider_is_built() -> None:
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        AnthropicProvider(api_key=None, timeout_seconds=30)


def test_dropped_constraints_are_described_so_the_model_still_sees_them() -> None:
    # Strict mode can't enforce `maximum` and friends; Pydantic still does, so
    # the model must be told the limits or it learns them only from errors.
    schema = strict_schema(
        {
            "type": "object",
            "properties": {
                "k": {"type": "integer", "description": "How many.", "minimum": 1, "maximum": 40},
                "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                "name": {"type": "string", "maxLength": 200, "description": "The name."},
            },
        }
    )

    properties = schema["properties"]
    assert properties["k"]["description"] == "How many. (minimum 1, maximum 40)"
    assert properties["tags"]["description"] == "(at most 5 items)"
    assert properties["name"]["description"] == "The name. (at most 200 characters)"
    assert "maximum" not in properties["k"]
