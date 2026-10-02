"""Validating real decks built from real fixture cards loaded into a real database."""

import json
from pathlib import Path
from uuid import UUID

import pytest

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.db.migrate import upgrade
from mtg_deck_advisor.deck.facts import CardFacts, load_card_facts
from mtg_deck_advisor.deck.state import DeckState
from mtg_deck_advisor.guardrails.commander import validate
from mtg_deck_advisor.ingestion.card_ingestion import apply_cards
from mtg_deck_advisor.ingestion.cards import CardRecord, normalise

FIXTURES = Path(__file__).parent.parent / "fixtures" / "scryfall"
FILES = (
    "oracle_cards_sample.jsonl",
    "oracle_cards_punctuation.jsonl",
    "oracle_cards_deckbuilding.jsonl",
)


def fixture_cards() -> list[CardRecord]:
    lines = [line for name in FILES for line in (FIXTURES / name).read_text("utf-8").splitlines()]
    return [record for line in lines if (record := normalise(json.loads(line))) is not None]


ID = {card.name: card.oracle_id for card in fixture_cards()}
NONBASICS = [
    "Sol Ring",
    "Lim-Dûl's Cohort",
    "Delver of Secrets // Insectile Aberration",
    "Kytheon, Hero of Akros // Gideon, Battle-Forged",
    "Glimpse the Unthinkable",
]


@pytest.fixture
def facts(settings: Settings) -> dict[UUID, CardFacts]:
    upgrade(settings)
    with connect(settings) as conn:
        apply_cards(conn, fixture_cards())
        return load_card_facts(conn, ID.values())


def atraxa_deck() -> tuple[DeckState, dict[UUID, int]]:
    """A legal deck of real cards: Atraxa, five nonbasics, 10 Relentless Rats,
    3 Snow-Covered Islands, and free basics. The pool holds exactly what is used."""
    cards = {ID[name]: 1 for name in NONBASICS}
    cards |= {ID["Relentless Rats"]: 10, ID["Snow-Covered Island"]: 3}
    cards |= {ID["Plains"]: 20, ID["Island"]: 20, ID["Swamp"]: 20, ID["Forest"]: 21}
    pool = {ID["Atraxa, Praetors' Voice"]: 1}
    pool |= {ID[name]: 1 for name in NONBASICS}
    pool |= {ID["Relentless Rats"]: 10, ID["Snow-Covered Island"]: 3}
    return DeckState.new(ID["Atraxa, Praetors' Voice"], cards), pool


def only_code(deck: DeckState, facts: dict[UUID, CardFacts], pool: dict[UUID, int]) -> str:
    [violation] = validate(deck, facts, pool).violations
    return violation.code


def test_a_real_legal_deck_validates_clean(facts: dict[UUID, CardFacts]) -> None:
    deck, pool = atraxa_deck()

    assert deck.total == 100
    assert validate(deck, facts, pool).valid


def test_each_rule_broken_alone_gives_exactly_its_violation(
    facts: dict[UUID, CardFacts],
) -> None:
    deck, pool = atraxa_deck()
    forest = ID["Forest"]

    def replacing_a_forest(name: str, owned: int = 1) -> tuple[DeckState, dict[UUID, int]]:
        return deck.without_card(forest).with_card(ID[name]), pool | {ID[name]: owned}

    assert (
        only_code(*replacing_a_forest("Bonecrusher Giant // Stomp"), facts=facts)
        == "color_identity"
    )
    assert only_code(*replacing_a_forest("Mox Jet"), facts=facts) == "banned"
    assert (
        only_code(
            deck.without_card(forest).with_card(ID["Sol Ring"]), facts, pool | {ID["Sol Ring"]: 2}
        )
        == "too_many_copies"
    )
    assert (
        only_code(deck.without_card(forest).with_card(ID["Relentless Rats"]), facts, pool)
        == "exceeds_owned"
    )
    assert only_code(deck, facts, pool | {ID["Glimpse the Unthinkable"]: 0}) == "not_in_pool"
    assert only_code(deck.without_card(forest), facts, pool) == "deck_size"


def test_a_planeswalker_that_can_be_a_commander_leads_a_legal_deck(
    facts: dict[UUID, CardFacts],
) -> None:
    teferi = ID["Teferi, Temporal Archmage"]
    deck = DeckState.new(teferi, {ID["Island"]: 99})

    assert validate(deck, facts, {teferi: 1}).valid


def test_a_creature_that_is_not_legendary_cannot_lead(facts: dict[UUID, CardFacts]) -> None:
    delver = ID["Delver of Secrets // Insectile Aberration"]
    deck = DeckState.new(delver, {ID["Island"]: 99})

    [violation] = validate(deck, facts, {delver: 1}).violations

    assert violation.code == "commander_not_eligible"
    assert violation.rule == "903.3"
