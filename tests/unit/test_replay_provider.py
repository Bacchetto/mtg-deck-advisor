"""Recording real responses, and replaying them with no network, for demos and evals."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from mtg_deck_advisor.llm.client import ModelClient
from mtg_deck_advisor.llm.fake import FakeProvider
from mtg_deck_advisor.llm.recording import MemoryRecorder
from mtg_deck_advisor.llm.replay import (
    RecordingProvider,
    ReplayMissError,
    ReplayProvider,
    request_key,
)
from mtg_deck_advisor.llm.types import (
    Message,
    ModelRequest,
    ProviderRequest,
    ProviderResponse,
    Usage,
)

MODEL = "claude-opus-5-5"


def provider_request(**changes: Any) -> ProviderRequest:
    values: dict[str, Any] = {
        "purpose": "role_tagging",
        "system": "You tag Magic cards.",
        "messages": (Message(role="user", content="Tag Sol Ring."),),
        "max_tokens": 300,
        "effort": "low",
        "output_schema": {"type": "object", "properties": {"roles": {"type": "array"}}},
    }
    return ProviderRequest(**(values | changes))


def reply(text: str = '{"roles": ["ramp"]}') -> ProviderResponse:
    return ProviderResponse(
        text=text,
        stop_reason="end_turn",
        usage=Usage(input_tokens=120, output_tokens=20),
        model=MODEL,
    )


# --- the request key -------------------------------------------------------


def test_the_same_request_always_gets_the_same_key() -> None:
    assert request_key(provider_request(), MODEL) == request_key(provider_request(), MODEL)


def test_the_key_ignores_the_purpose_label() -> None:
    # Renaming a purpose must not invalidate every recording made under it.
    assert request_key(provider_request(purpose="a"), MODEL) == request_key(
        provider_request(purpose="b"), MODEL
    )


@pytest.mark.parametrize(
    "change",
    [
        {"system": "You are someone else."},
        {"messages": (Message(role="user", content="Tag Mox Jet."),)},
        {"max_tokens": 301},
        {"effort": "high"},
        {"output_schema": None},
    ],
)
def test_anything_that_could_change_the_answer_changes_the_key(change: dict[str, Any]) -> None:
    assert request_key(provider_request(**change), MODEL) != request_key(provider_request(), MODEL)


def test_the_model_is_part_of_the_key() -> None:
    assert request_key(provider_request(), MODEL) != request_key(
        provider_request(), "claude-haiku-4-5"
    )


# --- record, then replay ---------------------------------------------------


def test_a_recorded_response_replays_with_no_provider_behind_it(tmp_path: Path) -> None:
    inner = FakeProvider(reply())
    RecordingProvider(inner, tmp_path).complete(provider_request(), MODEL)

    replayed = ReplayProvider(tmp_path).complete(provider_request(), MODEL)

    assert replayed == reply()
    assert len(inner.calls) == 1  # the real provider was called once, when recording


def test_recording_passes_the_real_providers_identity_through(tmp_path: Path) -> None:
    recording = RecordingProvider(FakeProvider(reply(), bills=True), tmp_path)

    assert (recording.name, recording.bills) == ("fake", True)


def test_a_recording_is_a_readable_json_file_named_by_its_key(tmp_path: Path) -> None:
    RecordingProvider(FakeProvider(reply()), tmp_path).complete(provider_request(), MODEL)

    [file] = list(tmp_path.glob("*.json"))
    data = json.loads(file.read_text(encoding="utf-8"))
    assert file.stem == request_key(provider_request(), MODEL)
    assert data["model"] == MODEL
    assert data["request"]["system"] == "You tag Magic cards."
    assert data["response"]["text"] == '{"roles": ["ramp"]}'


def test_replaying_is_free(tmp_path: Path) -> None:
    replay = ReplayProvider(tmp_path)

    assert (replay.name, replay.bills) == ("replay", False)


def test_an_unrecorded_request_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(ReplayMissError, match="RECORD_RESPONSES"):
        ReplayProvider(tmp_path).complete(provider_request(), MODEL)


# --- through the model client ----------------------------------------------


class Roles(BaseModel):
    roles: list[str]


def test_a_structured_call_with_a_retry_replays_exactly(tmp_path: Path) -> None:
    request = ModelRequest(
        purpose="role_tagging",
        system="You tag Magic cards.",
        messages=(Message(role="user", content="Tag Sol Ring."),),
        max_tokens=300,
    )
    # Recorded: an invalid first answer, then the corrected one.
    inner = FakeProvider(reply('{"roles": "ramp"}'), reply('{"roles": ["ramp"]}'))
    recorded = ModelClient(
        RecordingProvider(inner, tmp_path), MODEL, recorder=MemoryRecorder()
    ).generate_structured(request, Roles)

    replayed = ModelClient(
        ReplayProvider(tmp_path), MODEL, recorder=MemoryRecorder()
    ).generate_structured(request, Roles)

    assert replayed.value == recorded.value == Roles(roles=["ramp"])
    assert replayed.attempts == 2
    assert replayed.response.cost_usd == 0
    assert len(list(tmp_path.glob("*.json"))) == 2
