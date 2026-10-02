"""Stored role tags in a real database: saved with what produced them, read back by card."""

import json
from pathlib import Path

import pytest

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.db.migrate import upgrade
from mtg_deck_advisor.ingestion.card_ingestion import apply_cards
from mtg_deck_advisor.ingestion.cards import normalise
from mtg_deck_advisor.llm.roles import CardRoles, load_roles, save_roles

FIXTURE = Path(__file__).parent.parent / "fixtures" / "scryfall" / "oracle_cards_sample.jsonl"


@pytest.fixture
def loaded(settings: Settings) -> Settings:
    upgrade(settings)
    records = [
        record
        for line in FIXTURE.read_text(encoding="utf-8").splitlines()
        if (record := normalise(json.loads(line))) is not None
    ]
    with connect(settings) as conn:
        apply_cards(conn, records)
    return settings


def sol_ring_id(settings: Settings) -> object:
    with connect(settings) as conn:
        row = conn.execute("SELECT oracle_id FROM cards WHERE name = 'Sol Ring'").fetchone()
    assert row is not None
    return row[0]


def test_saved_roles_are_read_back_with_their_provenance(loaded: Settings) -> None:
    oracle_id = sol_ring_id(loaded)
    roles = CardRoles(
        oracle_id=oracle_id,
        roles=["ramp"],
        reason="Taps for two colorless mana.",
        content_hash="abc123",
        prompt_version="roles-v1",
        model="claude-sonnet-5-5",
    )

    with connect(loaded) as conn:
        save_roles(conn, [roles])
        stored = load_roles(conn, [oracle_id])

    assert stored == {oracle_id: roles}


def test_saving_again_replaces_the_previous_tags(loaded: Settings) -> None:
    oracle_id = sol_ring_id(loaded)
    first = CardRoles(
        oracle_id=oracle_id,
        roles=["ramp"],
        reason="v1",
        content_hash="h1",
        prompt_version="roles-v1",
        model="claude-haiku-4-5",
    )
    second = first.model_copy(
        update={"roles": ["ramp", "mana_fixing"], "prompt_version": "roles-v2"}
    )

    with connect(loaded) as conn:
        save_roles(conn, [first])
        save_roles(conn, [second])
        stored = load_roles(conn, [oracle_id])

    assert stored[oracle_id].roles == ["ramp", "mana_fixing"]
    assert stored[oracle_id].prompt_version == "roles-v2"
