"""Resolving pool entries to cards, against a real database holding real fixture cards."""

import json
import uuid
from pathlib import Path

import pytest

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.db.migrate import upgrade
from mtg_deck_advisor.ingestion.card_ingestion import apply_cards
from mtg_deck_advisor.ingestion.cards import CardRecord, normalise
from mtg_deck_advisor.ingestion.pool import PoolEntry
from mtg_deck_advisor.ingestion.resolve import ResolvedPool, resolve

FIXTURE = Path(__file__).parent.parent / "fixtures" / "scryfall" / "oracle_cards_sample.jsonl"


def fixture_cards() -> list[CardRecord]:
    """Sol Ring, Delver of Secrets, Bonecrusher Giant, Lim-Dûl's Cohort, Mox Jet (banned),
    and two Red Herrings: one Commander-legal, one a joke card."""
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    return [record for line in lines if (record := normalise(json.loads(line))) is not None]


def joke_herring(cards: list[CardRecord]) -> CardRecord:
    return next(c for c in cards if c.name == "Red Herring" and c.commander_legality == "not_legal")


@pytest.fixture
def loaded(settings: Settings) -> Settings:
    upgrade(settings)
    with connect(settings) as conn:
        apply_cards(conn, fixture_cards())
    return settings


def resolve_names(settings: Settings, *names: str) -> ResolvedPool:
    entries = [PoolEntry(name=name, quantity=1, line=i) for i, name in enumerate(names, 1)]
    with connect(settings) as conn:
        return resolve(conn, entries)


def matched_names(pool: ResolvedPool) -> list[str]:
    return [match.card.name for match in pool.matched]


def test_an_exact_name_matches(loaded: Settings) -> None:
    pool = resolve_names(loaded, "Sol Ring")

    [match] = pool.matched
    assert match.card.name == "Sol Ring"
    assert str(match.card.oracle_id) == "6ad8011d-3471-4369-9d68-b264cc027487"
    assert match.matched_by == "name"
    assert match.entry.line == 1


@pytest.mark.parametrize(
    "typed",
    ["sol ring", "SOL RING", "  Sol   Ring "],
)
def test_case_and_spacing_do_not_matter(loaded: Settings, typed: str) -> None:
    assert matched_names(resolve_names(loaded, typed)) == ["Sol Ring"]


@pytest.mark.parametrize(
    "typed",
    ["Lim-Dul's Cohort", "lim-dûl's cohort", "Lim-Dûl\u2019s Cohort"],
)
def test_accents_and_apostrophe_style_do_not_matter(loaded: Settings, typed: str) -> None:
    assert matched_names(resolve_names(loaded, typed)) == ["Lim-Dûl's Cohort"]


def test_a_front_face_name_finds_its_double_faced_card(loaded: Settings) -> None:
    pool = resolve_names(loaded, "Delver of Secrets", "bonecrusher giant")

    assert matched_names(pool) == [
        "Delver of Secrets // Insectile Aberration",
        "Bonecrusher Giant // Stomp",
    ]
    assert [match.matched_by for match in pool.matched] == ["front_face", "front_face"]


def test_a_full_double_faced_name_matches_with_one_or_two_slashes(loaded: Settings) -> None:
    pool = resolve_names(loaded, "Bonecrusher Giant // Stomp", "Bonecrusher Giant / Stomp")

    assert matched_names(pool) == ["Bonecrusher Giant // Stomp"] * 2


def test_a_commander_legal_card_wins_over_a_joke_card_of_the_same_name(loaded: Settings) -> None:
    [match] = resolve_names(loaded, "Red Herring").matched

    assert match.card.commander_legality == "legal"


def test_a_banned_card_still_matches(loaded: Settings) -> None:
    # Resolution finds cards; deciding what a deck may contain is the validator's job.
    [match] = resolve_names(loaded, "Mox Jet").matched

    assert match.card.commander_legality == "banned"


def test_a_name_shared_only_by_joke_cards_is_ambiguous(loaded: Settings) -> None:
    cards = fixture_cards()
    second_joke = joke_herring(cards).model_copy(update={"oracle_id": uuid.uuid4()})
    legal_removed = [c for c in cards if c.commander_legality != "legal" or c.name != "Red Herring"]
    with connect(loaded) as conn:
        apply_cards(conn, [*legal_removed, second_joke])

    pool = resolve_names(loaded, "Red Herring")

    assert pool.matched == []
    [ambiguous] = pool.ambiguous
    assert len(ambiguous.candidates) == 2
    assert {c.name for c in ambiguous.candidates} == {"Red Herring"}


def test_an_unknown_name_gets_suggestions(loaded: Settings) -> None:
    pool = resolve_names(loaded, "Sol Rnig")

    assert pool.matched == []
    [unknown] = pool.unknown
    assert unknown.entry.name == "Sol Rnig"
    assert unknown.suggestions[0] == "Sol Ring"


def test_a_name_like_nothing_gets_no_suggestions(loaded: Settings) -> None:
    [unknown] = resolve_names(loaded, "Qwxzv Plmkj").unknown

    assert unknown.suggestions == []


def test_a_removed_card_does_not_match(loaded: Settings) -> None:
    with connect(loaded) as conn:
        apply_cards(conn, [c for c in fixture_cards() if c.name != "Sol Ring"])

    pool = resolve_names(loaded, "Sol Ring")

    assert pool.matched == []
    assert [unknown.entry.name for unknown in pool.unknown] == ["Sol Ring"]


def test_every_entry_lands_in_exactly_one_group_in_input_order(loaded: Settings) -> None:
    pool = resolve_names(loaded, "Mox Jet", "Nonexistent Card", "Sol Ring", "sol ring")

    assert [m.entry.line for m in pool.matched] == [1, 3, 4]
    assert [u.entry.line for u in pool.unknown] == [2]
    assert pool.ambiguous == []
