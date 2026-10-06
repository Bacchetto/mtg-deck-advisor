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

FIXTURES = Path(__file__).parent.parent / "fixtures" / "scryfall"


def fixture_cards() -> list[CardRecord]:
    """Real cards: Sol Ring, Delver of Secrets, Bonecrusher Giant, Lim-Dûl's Cohort, Mox Jet
    (banned) and two Red Herrings (one Commander-legal, one a joke card); and, with
    punctuation in their names, Atraxa, Kytheon (double-faced), and the legal "Glimpse the
    Unthinkable" beside the joke "Glimpse, the Unthinkable"."""
    lines = [
        line
        for name in ("oracle_cards_sample.jsonl", "oracle_cards_punctuation.jsonl")
        for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines()
    ]
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


# --- ignoring punctuation --------------------------------------------------


def test_a_name_typed_without_punctuation_matches(loaded: Settings) -> None:
    pool = resolve_names(loaded, "Atraxa Praetors Voice", "Lim Duls Cohort", "lim-duls cohort")

    assert matched_names(pool) == [
        "Atraxa, Praetors' Voice",
        "Lim-Dûl's Cohort",
        "Lim-Dûl's Cohort",
    ]
    assert pool.matched[0].matched_by == "name_ignoring_punctuation"


def test_a_front_face_typed_without_punctuation_matches(loaded: Settings) -> None:
    [match] = resolve_names(loaded, "Kytheon Hero of Akros").matched

    assert match.card.name == "Kytheon, Hero of Akros // Gideon, Battle-Forged"
    assert match.matched_by == "front_face_ignoring_punctuation"


def test_an_exact_name_wins_over_a_punctuation_free_match(loaded: Settings) -> None:
    # Typed with its comma, this is the joke card's exact name.
    [match] = resolve_names(loaded, "Glimpse, the Unthinkable").matched

    assert match.card.commander_legality == "not_legal"
    assert match.matched_by == "name"


def test_punctuation_collisions_resolve_to_the_commander_legal_card(loaded: Settings) -> None:
    # Only the punctuation-free step matches, and it finds both Glimpses.
    [match] = resolve_names(loaded, "Glimpse the Unthinkable.").matched

    assert match.card.name == "Glimpse the Unthinkable"
    assert match.card.commander_legality == "legal"
    assert match.matched_by == "name_ignoring_punctuation"


def test_a_misspelling_without_punctuation_still_gets_a_suggestion(loaded: Settings) -> None:
    [unknown] = resolve_names(loaded, "Atraxa Praetor Voice").unknown

    assert unknown.suggestions[0] == "Atraxa, Praetors' Voice"


# --- names with variant tags (#117) -------------------------------------------


@pytest.mark.parametrize(
    ("typed", "card"),
    [
        ("Sol Ring (C18)", "Sol Ring"),
        ("Sol Ring (Showcase)", "Sol Ring"),
        (
            "Delver of Secrets (Showcase) (Step-and-Compleat Foil)",
            "Delver of Secrets // Insectile Aberration",
        ),
        ("Atraxa Praetors Voice (Halo Foil)", "Atraxa, Praetors' Voice"),
    ],
)
def test_trailing_variant_tags_are_ignored_when_the_name_matches_nothing(
    loaded: Settings, typed: str, card: str
) -> None:
    [match] = resolve_names(loaded, typed).matched

    assert match.card.name == card
    assert match.entry.name == typed  # the entry keeps what the user's file said


def test_a_real_name_ending_in_parentheses_wins_over_the_name_without_them(
    loaded: Settings,
) -> None:
    # A real card can end in parentheses, such as "Hazmat Suit (Used)".
    line = next(
        line
        for line in (FIXTURES / "oracle_cards_sample.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if json.loads(line)["name"] == "Sol Ring"
    )
    raw = json.loads(line) | {"name": "Sol Ring (Used)", "oracle_id": str(uuid.uuid4())}
    used = normalise(raw)
    assert used is not None
    with connect(loaded) as conn:
        apply_cards(conn, [*fixture_cards(), used])

    assert matched_names(resolve_names(loaded, "Sol Ring (Used)", "Sol Ring (C18)")) == [
        "Sol Ring (Used)",
        "Sol Ring",
    ]


def test_a_tagged_name_that_still_matches_nothing_is_unknown_as_given(loaded: Settings) -> None:
    pool = resolve_names(loaded, "Not A Real Card (Showcase)")

    [unknown] = pool.unknown
    assert unknown.entry.name == "Not A Real Card (Showcase)"
