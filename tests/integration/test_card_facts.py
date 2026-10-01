"""Loading card facts from a real database holding real fixture cards."""

import json
import uuid
from pathlib import Path

import pytest

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.db.migrate import upgrade
from mtg_deck_advisor.deck.facts import load_card_facts
from mtg_deck_advisor.ingestion.card_ingestion import apply_cards
from mtg_deck_advisor.ingestion.cards import CardRecord, normalise

FIXTURES = Path(__file__).parent.parent / "fixtures" / "scryfall"
SOL_RING = uuid.UUID("6ad8011d-3471-4369-9d68-b264cc027487")


def fixture_cards() -> list[CardRecord]:
    lines = [
        line
        for name in ("oracle_cards_sample.jsonl", "oracle_cards_punctuation.jsonl")
        for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines()
    ]
    return [record for line in lines if (record := normalise(json.loads(line))) is not None]


@pytest.fixture
def loaded(settings: Settings) -> Settings:
    upgrade(settings)
    with connect(settings) as conn:
        apply_cards(conn, fixture_cards())
    return settings


def ids(*names: str) -> list[uuid.UUID]:
    by_name = {card.name: card.oracle_id for card in fixture_cards()}
    return [by_name[name] for name in names]


def test_facts_are_loaded_for_the_requested_cards(loaded: Settings) -> None:
    atraxa, mox_jet = ids("Atraxa, Praetors' Voice", "Mox Jet")

    with connect(loaded) as conn:
        facts = load_card_facts(conn, [atraxa, mox_jet])

    assert set(facts) == {atraxa, mox_jet}
    assert facts[atraxa].name == "Atraxa, Praetors' Voice"
    assert facts[atraxa].color_identity == frozenset({"W", "U", "B", "G"})
    assert facts[atraxa].commander_eligibility.eligible
    assert facts[mox_jet].commander_legality == "banned"
    assert facts[mox_jet].copy_limit == 1


def test_unknown_and_removed_cards_are_absent(loaded: Settings) -> None:
    with connect(loaded) as conn:
        apply_cards(conn, [c for c in fixture_cards() if c.oracle_id != SOL_RING])
        facts = load_card_facts(conn, [SOL_RING, uuid.uuid4()])

    assert facts == {}
