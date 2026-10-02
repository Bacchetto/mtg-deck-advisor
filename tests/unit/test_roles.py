"""Role tagging: the prompt, the batch schema, and checking the answer covers every card."""

import json
import uuid
from typing import get_args

import pytest

from mtg_deck_advisor.llm.client import ModelClient
from mtg_deck_advisor.llm.errors import InvalidOutputError
from mtg_deck_advisor.llm.fake import FakeProvider
from mtg_deck_advisor.llm.recording import MemoryRecorder
from mtg_deck_advisor.llm.roles import (
    PROMPT_VERSION,
    ROLE_DEFINITIONS,
    CardToTag,
    Role,
    build_request,
    tag_cards,
)
from mtg_deck_advisor.llm.types import ProviderResponse, Usage

SOL_RING = CardToTag(
    oracle_id=uuid.UUID("6ad8011d-3471-4369-9d68-b264cc027487"),
    name="Sol Ring",
    type_line="Artifact",
    mana_cost="{1}",
    oracle_text="{T}: Add {C}{C}.",
)
DAY_OF_JUDGMENT = CardToTag(
    oracle_id=uuid.uuid4(),
    name="Day of Judgment",
    type_line="Sorcery",
    mana_cost="{2}{W}{W}",
    oracle_text="Destroy all creatures.",
)


def answer(*cards: dict[str, object]) -> ProviderResponse:
    return ProviderResponse(
        text=json.dumps({"cards": list(cards)}),
        stop_reason="end_turn",
        usage=Usage(input_tokens=500, output_tokens=60),
        model="claude-opus-5-5",
    )


def client_with(*replies: ProviderResponse) -> tuple[ModelClient, FakeProvider]:
    provider = FakeProvider(*replies)
    return ModelClient(provider, "claude-opus-5-5", recorder=MemoryRecorder()), provider


def test_every_role_has_a_definition_in_the_prompt() -> None:
    request = build_request([SOL_RING])

    assert set(ROLE_DEFINITIONS) == set(get_args(Role))
    for role, definition in ROLE_DEFINITIONS.items():
        assert f"{role}: {definition}" in request.system


def test_the_system_prompt_is_the_same_for_every_batch() -> None:
    # A stable prefix is what prompt caching reuses across batches.
    assert build_request([SOL_RING]).system == build_request([DAY_OF_JUDGMENT]).system


def test_each_card_is_numbered_with_its_full_text() -> None:
    request = build_request([SOL_RING, DAY_OF_JUDGMENT])

    content = request.messages[0].content
    assert "1. Sol Ring | {1} | Artifact | {T}: Add {C}{C}." in content
    assert "2. Day of Judgment | {2}{W}{W} | Sorcery | Destroy all creatures." in content
    assert request.purpose == "role_tagging"


def test_roles_come_back_keyed_by_card() -> None:
    client, _ = client_with(
        answer(
            {"index": 1, "roles": ["ramp"], "reason": "Taps for two mana."},
            {"index": 2, "roles": ["board_wipe"], "reason": "Destroys all creatures."},
        )
    )

    tagged = tag_cards(client, [SOL_RING, DAY_OF_JUDGMENT])

    assert tagged[SOL_RING.oracle_id].roles == ["ramp"]
    assert tagged[DAY_OF_JUDGMENT.oracle_id].roles == ["board_wipe"]
    assert tagged[SOL_RING.oracle_id].reason == "Taps for two mana."


def test_a_card_can_have_no_roles() -> None:
    client, _ = client_with(answer({"index": 1, "roles": [], "reason": "A vanilla card."}))

    assert tag_cards(client, [SOL_RING])[SOL_RING.oracle_id].roles == []


def test_a_role_outside_the_taxonomy_is_invalid_output() -> None:
    # Retried once with the validation error, then rejected.
    bad = answer({"index": 1, "roles": ["mana_rock"], "reason": "..."})
    client, provider = client_with(bad, bad)

    with pytest.raises(InvalidOutputError):
        tag_cards(client, [SOL_RING])

    assert len(provider.calls) == 2


def test_an_answer_that_skips_or_repeats_a_card_is_rejected() -> None:
    client, _ = client_with(answer({"index": 1, "roles": ["ramp"], "reason": "..."}))

    with pytest.raises(InvalidOutputError, match="expected 1, 2"):
        tag_cards(client, [SOL_RING, DAY_OF_JUDGMENT])


def test_the_prompt_version_is_recorded_with_the_request() -> None:
    assert PROMPT_VERSION in build_request([SOL_RING]).system
