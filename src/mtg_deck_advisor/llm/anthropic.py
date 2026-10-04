"""Claude, through the official Anthropic SDK, behind the project's Provider protocol (MOD-4).

The SDK handles transport: the timeout, and retries of connection errors, 408,
409, 429 and 5xx with backoff. This module turns a ProviderRequest into a
Messages API request and the reply back into plain text and usage. ModelClient
does the rest (validation, budgets, recording). See ADR 0008.

Per current Anthropic guidance:
- The system prompt is marked for prompt caching: it's the stable prefix of
  every request for a purpose.
- Structured output is requested as a strict JSON schema (`output_config.format`).
  ModelClient still validates every reply against the full Pydantic model.
- Opus and Sonnet 5.5 opt into server-side refusal fallbacks, so a declined
  request is retried on Anthropic's recommended model inside the same call.
"""

import copy
from typing import Any

import anthropic
import httpx2

from mtg_deck_advisor.llm.errors import ProviderAuthError, SpendLimitError
from mtg_deck_advisor.llm.types import (
    Message,
    ProviderRequest,
    ProviderResponse,
    ToolCall,
    ToolSpec,
    Usage,
)

# Models that accept output_config.effort. Haiku 4.5 rejects it.
EFFORT_MODELS = frozenset(
    {"claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5", "claude-sonnet-5", "claude-opus-4-8"}
)
# Models where Anthropic recommends opting into server-side refusal fallbacks.
FALLBACK_MODELS = frozenset({"claude-opus-5-5", "claude-sonnet-5-5"})
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# JSON Schema keywords structured outputs don't support. They're dropped from
# the schema sent to the API; ModelClient still enforces them when it
# validates the reply against the Pydantic model.
UNSUPPORTED_SCHEMA_KEYS = frozenset(
    {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
        "uniqueItems",
    }
)

# Phrases that mark a spend-limit or billing refusal (rate-limits documentation).
SPEND_LIMIT_MESSAGES = (
    "you have reached your specified api usage limits",
    "you have reached your specified workspace api usage limits",
    "credit balance is too low",
)


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """A copy of `schema` in the form structured outputs require.

    Every object gets `additionalProperties: false`, and unsupported
    constraints are removed. The input is not modified.
    """

    def tighten(node: Any) -> Any:
        if isinstance(node, dict):
            result = {k: tighten(v) for k, v in node.items() if k not in UNSUPPORTED_SCHEMA_KEYS}
            if result.get("type") == "object":
                result["additionalProperties"] = False
            return result
        if isinstance(node, list):
            return [tighten(item) for item in node]
        return node

    tightened: dict[str, Any] = tighten(copy.deepcopy(schema))
    return tightened


class AnthropicProvider:
    def __init__(
        self,
        *,
        api_key: str | None,
        timeout_seconds: float,
        max_retries: int = 2,
        http_client: httpx2.Client | None = None,
    ) -> None:
        if not api_key:
            raise ValueError(
                "the anthropic provider needs ANTHROPIC_API_KEY (set it in .env, never in code)"
            )
        options: dict[str, Any] = {
            "api_key": api_key,
            "timeout": timeout_seconds,
            "max_retries": max_retries,
        }
        if http_client is not None:  # tests inject a fake transport
            options["http_client"] = http_client
        self._client = anthropic.Anthropic(**options)

    @property
    def name(self) -> str:
        return "anthropic"

    @property
    def bills(self) -> bool:
        return True

    def complete(self, request: ProviderRequest, model: str) -> ProviderResponse:
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_tokens,
            "system": [
                {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
            ],
            "messages": [_message_param(message) for message in request.messages],
        }
        if request.tools:
            # The model decides whether to call a tool ("auto", the default):
            # forced tool use returns a 400 on Sonnet and Opus 5.5.
            params["tools"] = [_tool_param(tool) for tool in request.tools]
        output_config: dict[str, Any] = {}
        if request.output_schema is not None:
            output_config["format"] = {
                "type": "json_schema",
                "schema": strict_schema(request.output_schema),
            }
        if request.effort is not None and model in EFFORT_MODELS:
            output_config["effort"] = request.effort
        if output_config:
            params["output_config"] = output_config

        try:
            if model in FALLBACK_MODELS:
                message: Any = self._client.beta.messages.create(
                    **params, betas=[FALLBACK_BETA], fallbacks="default"
                )
            else:
                message = self._client.messages.create(**params)
        except anthropic.AuthenticationError as exc:
            raise ProviderAuthError(
                f"Anthropic rejected the API key: check ANTHROPIC_API_KEY ({exc.message})"
            ) from exc
        except anthropic.APIStatusError as exc:
            if _is_spend_limit(exc):
                raise SpendLimitError(f"Anthropic spend limit reached: {exc.message}") from exc
            raise

        text = "".join(block.text for block in message.content if block.type == "text")
        usage = message.usage
        return ProviderResponse(
            text=text,
            stop_reason=message.stop_reason or "",
            model=message.model,
            tool_calls=tuple(
                ToolCall(id=block.id, name=block.name, arguments=dict(block.input))
                for block in message.content
                if block.type == "tool_use"
            ),
            # Every block as the API sent it, to send back unchanged: thinking
            # blocks on a tool-use turn must be returned as they were (ADR 0012).
            provider_content=tuple(block.to_dict(mode="json") for block in message.content),
            usage=Usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_input_tokens=usage.cache_read_input_tokens or 0,
                cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
            ),
        )


def _tool_param(tool: ToolSpec) -> dict[str, Any]:
    """A tool definition: strict, so the model's arguments always match the schema."""
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": strict_schema(tool.input_schema),
        "strict": True,
    }


def _message_param(message: Message) -> dict[str, Any]:
    """One turn in the Messages API's shape."""
    if message.role == "assistant" and message.provider_content is not None:
        # A turn this provider produced goes back exactly as it came.
        return {"role": "assistant", "content": list(message.provider_content)}
    if not message.tool_calls and not message.tool_results:
        return {"role": message.role, "content": message.content}
    blocks: list[dict[str, Any]] = []
    for result in message.tool_results:
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": result.tool_call_id,
            "content": result.content,
        }
        if result.is_error:
            block["is_error"] = True
        blocks.append(block)
    if message.content:
        blocks.append({"type": "text", "text": message.content})
    blocks.extend(
        {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
        for call in message.tool_calls
    )
    return {"role": message.role, "content": blocks}


def _is_spend_limit(exc: anthropic.APIStatusError) -> bool:
    """A spend limit or billing refusal, as opposed to an ordinary bad request or rate limit."""
    body = exc.body if isinstance(exc.body, dict) else {}
    error = body.get("error", {}) if isinstance(body.get("error"), dict) else {}
    details = error.get("details", {}) if isinstance(error.get("details"), dict) else {}
    if details.get("error_code") == "enforced_spend_limit_reached":
        return True
    message = str(error.get("message", exc.message)).lower()
    return any(phrase in message for phrase in SPEND_LIMIT_MESSAGES)
