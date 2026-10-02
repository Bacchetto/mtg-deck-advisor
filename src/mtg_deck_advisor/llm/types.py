"""The types every model call goes through, whichever provider serves it (MOD-1).

These are the project's own types, not a provider SDK's, so the rest of the app
never depends on which provider is configured. See ADR 0008.
"""

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class Message(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Literal["user", "assistant"]
    content: str


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


class ProviderRequest(ModelRequest):
    """A request as handed to a provider: with the JSON schema the output must follow, if any."""

    output_schema: dict[str, Any] | None = None


class Usage(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


class ProviderResponse(BaseModel):
    """What a provider returns: raw text, before any validation."""

    model_config = ConfigDict(frozen=True)

    text: str
    # "end_turn", "max_tokens", "refusal", ...
    stop_reason: str
    usage: Usage
    # The model that actually answered (a fallback may differ from the one asked).
    model: str


class Provider(Protocol):
    """A source of model responses: a hosted API, a local model, recorded responses, a fake."""

    @property
    def name(self) -> str: ...

    @property
    def bills(self) -> bool:
        """Whether calls cost money, and so must be priced and budgeted."""
        ...

    def complete(self, request: ProviderRequest, model: str) -> ProviderResponse: ...
