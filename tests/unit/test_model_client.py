"""The model interface, with a scripted fake provider: no network, no paid calls."""

import pytest
from pydantic import BaseModel

from mtg_deck_advisor.llm.client import (
    BudgetExceededError,
    InvalidOutputError,
    ModelCallError,
    ModelClient,
    RefusalError,
)
from mtg_deck_advisor.llm.errors import SpendLimitError
from mtg_deck_advisor.llm.fake import FakeProvider
from mtg_deck_advisor.llm.recording import MemoryRecorder
from mtg_deck_advisor.llm.types import Message, ModelRequest, ProviderResponse, Usage
from mtg_deck_advisor.observability.tracing import traced

OPUS = "claude-opus-5-5"


class Roles(BaseModel):
    roles: list[str]


def request(text: str = "Tag Sol Ring.", max_tokens: int = 100) -> ModelRequest:
    return ModelRequest(
        purpose="role_tagging",
        system="You tag Magic cards.",
        messages=(Message(role="user", content=text),),
        max_tokens=max_tokens,
    )


def reply(text: str, *, input_tokens: int = 1000, output_tokens: int = 100) -> ProviderResponse:
    return ProviderResponse(
        text=text,
        stop_reason="end_turn",
        usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens),
        model=OPUS,
    )


def client_with(
    *replies: ProviderResponse | Exception, cap: float | None = None, bills: bool = True
) -> tuple[ModelClient, FakeProvider, MemoryRecorder]:
    provider = FakeProvider(*replies, bills=bills)
    recorder = MemoryRecorder()
    return ModelClient(provider, OPUS, recorder=recorder, cost_cap_usd=cap), provider, recorder


# --- plain calls -----------------------------------------------------------


def test_a_call_returns_text_with_cost_from_usage() -> None:
    client, _, _ = client_with(reply("Ramp.", input_tokens=1000, output_tokens=100))

    response = client.generate(request())

    assert response.text == "Ramp."
    # Opus 5.5: $4 per million input tokens, $20 per million output tokens.
    assert response.cost_usd == pytest.approx(1000 * 4 / 1e6 + 100 * 20 / 1e6)
    assert response.latency_ms >= 0
    assert client.spent_usd == pytest.approx(response.cost_usd)


def test_every_call_is_recorded_with_the_current_trace_id() -> None:
    client, _, recorder = client_with(reply("Ramp."))

    with traced() as trace_id:
        client.generate(request())

    [record] = recorder.records
    assert record.trace_id == trace_id
    assert record.purpose == "role_tagging"
    assert record.provider == "fake"
    assert record.model == OPUS
    assert record.outcome == "ok"
    assert record.request["messages"] == [{"role": "user", "content": "Tag Sol Ring."}]
    assert record.response_text == "Ramp."
    assert (record.input_tokens, record.output_tokens) == (1000, 100)


def test_a_provider_that_does_not_bill_costs_nothing() -> None:
    client, _, _ = client_with(reply("Ramp."), bills=False)

    assert client.generate(request()).cost_usd == 0


def test_an_unpriced_model_on_a_billing_provider_is_rejected_up_front() -> None:
    with pytest.raises(ValueError, match="no price"):
        ModelClient(FakeProvider(), "claude-imaginary-9", recorder=MemoryRecorder())


# --- structured output (MOD-2) ---------------------------------------------


def test_structured_output_is_parsed_and_validated() -> None:
    client, provider, _ = client_with(reply('{"roles": ["ramp"]}'))

    result = client.generate_structured(request(), Roles)

    assert result.value == Roles(roles=["ramp"])
    [sent] = provider.calls
    assert sent.output_schema == Roles.model_json_schema()


def test_an_invalid_response_is_retried_once_with_the_validation_error() -> None:
    client, provider, recorder = client_with(
        reply('{"roles": "ramp"}'), reply('{"roles": ["ramp"]}')
    )

    result = client.generate_structured(request(), Roles)

    assert result.value == Roles(roles=["ramp"])
    first, second = provider.calls
    # The retry shows the model its invalid answer and what was wrong with it.
    assert second.messages[:2] == (
        *first.messages,
        Message(role="assistant", content='{"roles": "ramp"}'),
    )
    assert "roles" in second.messages[2].content
    assert [r.outcome for r in recorder.records] == ["invalid_output", "ok"]
    assert [r.attempt for r in recorder.records] == [1, 2]


def test_a_response_still_invalid_after_the_retry_is_rejected_not_passed_through() -> None:
    client, provider, recorder = client_with(reply("not json"), reply('{"wrong": 1}'))

    with pytest.raises(InvalidOutputError):
        client.generate_structured(request(), Roles)

    assert len(provider.calls) == 2
    assert [r.outcome for r in recorder.records] == ["invalid_output", "invalid_output"]


# --- refusals and errors ---------------------------------------------------


def test_a_refusal_raises_and_is_recorded() -> None:
    refused = reply("")
    refused = refused.model_copy(update={"stop_reason": "refusal"})
    client, _, recorder = client_with(refused)

    with pytest.raises(RefusalError):
        client.generate(request())

    assert recorder.records[0].outcome == "refusal"


def test_a_provider_failure_raises_and_is_recorded_with_its_error() -> None:
    client, _, recorder = client_with(ConnectionError("network unreachable"))

    with pytest.raises(ModelCallError, match="network unreachable"):
        client.generate(request())

    [record] = recorder.records
    assert record.outcome == "error"
    assert record.error is not None and "network unreachable" in record.error


# --- budgets (MOD-3) -------------------------------------------------------


def test_a_call_that_could_exceed_the_cost_cap_is_refused_before_it_is_made() -> None:
    # Worst case for this request: its input plus max_tokens of output.
    client, provider, recorder = client_with(reply("Ramp."), cap=0.001)

    with pytest.raises(BudgetExceededError):
        client.generate(request(max_tokens=1000))

    assert provider.calls == []
    assert recorder.records[0].outcome == "budget_exceeded"


def test_spending_accumulates_until_the_cap_stops_further_calls() -> None:
    expensive = reply("Ramp.", input_tokens=1000, output_tokens=1000)  # $0.024
    client, provider, _ = client_with(expensive, expensive, cap=0.025)

    client.generate(request(max_tokens=50))
    with pytest.raises(BudgetExceededError):
        client.generate(request(max_tokens=50))

    assert len(provider.calls) == 1
    assert client.spent_usd == pytest.approx(0.024)


def test_a_provider_error_that_is_already_specific_keeps_its_type() -> None:
    client, _, recorder = client_with(SpendLimitError("credit balance is too low"))

    with pytest.raises(SpendLimitError):
        client.generate(request())

    assert recorder.records[0].outcome == "error"


def test_a_reply_from_an_unpriced_model_is_costed_at_the_requested_models_price() -> None:
    # A server-side fallback can answer from a model the price table lacks;
    # the money is already spent, so the call must not fail on costing it.
    unknown = reply("Ramp.", input_tokens=1000, output_tokens=100).model_copy(
        update={"model": "claude-unlisted-1"}
    )
    client, _, _ = client_with(unknown)

    response = client.generate(request())

    assert response.model == "claude-unlisted-1"
    assert response.cost_usd == pytest.approx(1000 * 4 / 1e6 + 100 * 20 / 1e6)
