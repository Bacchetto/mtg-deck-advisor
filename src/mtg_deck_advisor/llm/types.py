"""The types every model call goes through, whichever provider serves it (MOD-1).

These are the project's own types, not a provider SDK's, so the rest of the app
never depends on which provider is configured. See ADR 0008.
"""

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, model_validator

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class ToolSpec(BaseModel):
    """A tool the model may call: its name, what it does, and its arguments' JSON schema."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    input_schema: dict[str, Any]


class ToolCall(BaseModel):
    """A call the model asked for. The ID ties it to its result."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    arguments: dict[str, Any]


class ToolResult(BaseModel):
    """What running a tool call returned; `is_error` marks a failed or refused call."""

    model_config = ConfigDict(frozen=True)

    tool_call_id: str
    content: str
    is_error: bool = False


class Message(BaseModel):
    """One turn of a conversation.

    A user turn has text and/or tool results; an assistant turn has text and/or
    tool calls. An assistant turn can also keep `provider_content`: the
    provider's own blocks for that turn, verbatim. Providers that require a
    turn to be sent back unchanged (Claude's thinking blocks on tool-use
    turns) use it; others ignore it. See ADR 0012.
    """

    model_config = ConfigDict(frozen=True)

    role: Literal["user", "assistant"]
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolResult, ...] = ()
    provider_content: tuple[dict[str, Any], ...] | None = None

    @model_validator(mode="after")
    def _parts_match_the_role(self) -> "Message":
        if self.role == "user" and (self.tool_calls or self.provider_content is not None):
            raise ValueError("only an assistant turn can carry tool calls or provider content")
        if self.role == "assistant" and self.tool_results:
            raise ValueError("only a user turn can carry tool results")
        return self


class ModelRequest(BaseModel):
    """What the app asks of a model. Provider-neutral."""

    model_config = ConfigDict(frozen=True)

    # What the call is for, such as "role_tagging": recorded with every call so
    # cost and failures can be broken down by use.
    purpose: str
    system: str
    messages: tuple[Message, ...]
    max_tokens: int = 1024
    # How hard the model should think, on models that support it.
    effort: Effort | None = None
    # Tools the model may call; it decides whether and when (no forced calls).
    tools: tuple[ToolSpec, ...] = ()


class ProviderRequest(ModelRequest):
    """A request as handed to a provider: with the JSON schema the output must follow, if any."""

    output_schema: dict[str, Any] | None = None


class Usage(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


def request_dump(request: BaseModel, **kwargs: Any) -> dict[str, Any]:
    """A request as JSON, with tool fields left out when they're empty.

    A text-only request dumps exactly as it did before tool calling existed,
    so its replay key and its recorded form don't change.
    """
    content: dict[str, Any] = request.model_dump(mode="json", **kwargs)
    if not content.get("tools"):
        content.pop("tools", None)
    for message in content.get("messages", []):
        for field in ("tool_calls", "tool_results"):
            if not message[field]:
                del message[field]
        if message["provider_content"] is None:
            del message["provider_content"]
    return content


class ProviderResponse(BaseModel):
    """What a provider returns: raw text, before any validation."""

    model_config = ConfigDict(frozen=True)

    text: str
    # "end_turn", "max_tokens", "refusal", ...
    stop_reason: str
    usage: Usage
    # The model that actually answered (a fallback may differ from the one asked).
    model: str
    tool_calls: tuple[ToolCall, ...] = ()
    # The provider's own blocks for this turn, to send back unchanged (ADR 0012).
    provider_content: tuple[dict[str, Any], ...] | None = None


class Provider(Protocol):
    """A source of model responses: a hosted API, a local model, recorded responses, a fake."""

    @property
    def name(self) -> str: ...

    @property
    def bills(self) -> bool:
        """Whether calls cost money, and so must be priced and budgeted."""
        ...

    def complete(self, request: ProviderRequest, model: str) -> ProviderResponse: ...
