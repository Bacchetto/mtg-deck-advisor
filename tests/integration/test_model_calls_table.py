"""Model calls recorded in a real database, retrievable by trace ID (OBS-1, OBS-2)."""

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.db.migrate import upgrade
from mtg_deck_advisor.llm.client import ModelClient
from mtg_deck_advisor.llm.fake import FakeProvider
from mtg_deck_advisor.llm.recording import DatabaseRecorder
from mtg_deck_advisor.llm.types import Message, ModelRequest, ProviderResponse, Usage
from mtg_deck_advisor.observability.tracing import traced


def test_a_call_can_be_reconstructed_from_its_trace_id(settings: Settings) -> None:
    upgrade(settings)
    provider = FakeProvider(
        ProviderResponse(
            text="Ramp.",
            stop_reason="end_turn",
            usage=Usage(input_tokens=1000, output_tokens=100),
            model="claude-opus-5-5",
        )
    )
    client = ModelClient(provider, "claude-opus-5-5", recorder=DatabaseRecorder(settings))
    request = ModelRequest(
        purpose="role_tagging",
        system="You tag Magic cards.",
        messages=(Message(role="user", content="Tag Sol Ring."),),
        max_tokens=100,
    )

    with traced() as trace_id:
        client.generate(request)

    with connect(settings) as conn:
        row = conn.execute(
            """
            SELECT purpose, provider, model, outcome, request->>'system',
                   response_text, input_tokens, output_tokens, cost_usd
            FROM model_calls WHERE trace_id = %s
            """,
            (trace_id,),
        ).fetchone()

    assert row is not None
    purpose, provider_name, model, outcome, system, text, input_tokens, output_tokens, cost = row
    assert (purpose, provider_name, model, outcome) == (
        "role_tagging",
        "fake",
        "claude-opus-5-5",
        "ok",
    )
    assert system == "You tag Magic cards."
    assert (text, input_tokens, output_tokens) == ("Ramp.", 1000, 100)
    assert float(cost) == 0.006
