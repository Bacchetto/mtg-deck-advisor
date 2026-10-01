"""Commander rule building blocks, tested with real cards' type lines and rules text."""

import uuid

import pytest

from mtg_deck_advisor.deck.facts import (
    CardFacts,
    commander_eligibility,
    copy_limit,
    is_basic_land,
    is_free_basic,
)

TEFERI_TEXT = (
    "+1: Look at the top two cards of your library. Put one of them into your hand and the "
    "other on the bottom of your library.\n\u22121: Untap up to four target permanents.\n"
    "Teferi, Temporal Archmage can be your commander."
)
RATS_TEXT = (
    "This creature gets +1/+1 for each other creature on the battlefield named Relentless "
    "Rats.\nA deck can have any number of cards named Relentless Rats."
)
DWARVES_TEXT = (
    "This creature gets +1/+1 for each other creature named Seven Dwarves you control.\n"
    "A deck can have up to seven cards named Seven Dwarves."
)
NAZGUL_TEXT = "Deathtouch\nA deck can have up to nine cards named Nazgûl."


# --- basic lands -----------------------------------------------------------


@pytest.mark.parametrize(
    ("type_line", "basic"),
    [
        ("Basic Land — Plains", True),
        ("Basic Snow Land — Island", True),
        ("Basic Land", True),  # Wastes
        ("Land — Plains Island", False),  # Tundra: basic land types, but not a basic land
        ("Land Creature — Forest Dryad", False),  # Dryad Arbor
        ("Artifact", False),
    ],
)
def test_is_basic_land_reads_the_basic_supertype(type_line: str, basic: bool) -> None:
    assert is_basic_land(type_line) is basic


@pytest.mark.parametrize(
    ("name", "free"),
    [
        ("Plains", True),
        ("Island", True),
        ("Swamp", True),
        ("Mountain", True),
        ("Forest", True),
        ("Snow-Covered Island", False),  # basic, but must come from the pool
        ("Wastes", False),
        ("Tundra", False),
    ],
)
def test_only_the_five_basics_are_free(name: str, free: bool) -> None:
    assert is_free_basic(name) is free


# --- copy limits (903.5b) --------------------------------------------------


def test_an_ordinary_card_is_limited_to_one_copy() -> None:
    assert copy_limit("Sol Ring", "{T}: Add {C}{C}.", "Artifact") == 1


def test_basic_lands_have_no_copy_limit() -> None:
    assert copy_limit("Snow-Covered Island", "({T}: Add {U}.)", "Basic Snow Land — Island") is None


def test_any_number_in_the_cards_own_text_means_no_limit() -> None:
    assert copy_limit("Relentless Rats", RATS_TEXT, "Creature — Rat") is None


@pytest.mark.parametrize(
    ("name", "text", "limit"),
    [("Seven Dwarves", DWARVES_TEXT, 7), ("Nazgûl", NAZGUL_TEXT, 9)],
)
def test_up_to_n_in_the_cards_own_text_sets_the_limit(name: str, text: str, limit: int) -> None:
    assert copy_limit(name, text, "Creature") == limit


def test_an_exception_naming_a_different_card_does_not_apply() -> None:
    # Only a card's statement about *itself* changes its own limit.
    text = "A deck can have any number of cards named Relentless Rats."

    assert copy_limit("Rat Catcher", text, "Creature — Human") == 1


# --- commander eligibility (903.3, 903.3a) --------------------------------


@pytest.mark.parametrize(
    ("name", "type_line", "power", "toughness"),
    [
        ("Atraxa, Praetors' Voice", "Legendary Creature — Phyrexian Angel Horror", "4", "4"),
        ("Erebos, God of the Dead", "Legendary Enchantment Creature — God", "5", "7"),
        ("Golos, Tireless Pilgrim", "Legendary Artifact Creature — Scout", "3", "5"),
        ("Adrestia", "Legendary Artifact — Vehicle", "4", "4"),
        ("Inspirit, Flagship Vessel", "Legendary Artifact — Spacecraft", "5", "5"),
        (
            "Kytheon, Hero of Akros // Gideon, Battle-Forged",
            "Legendary Creature — Human Soldier // Legendary Planeswalker — Gideon",
            "2",
            "1",
        ),
    ],
)
def test_legendary_creatures_vehicles_and_spacecraft_with_stats_are_eligible(
    name: str, type_line: str, power: str, toughness: str
) -> None:
    eligibility = commander_eligibility(name, type_line, "", power, toughness)

    assert eligibility.eligible
    assert eligibility.rule == "903.3"


def test_a_spacecraft_without_power_and_toughness_is_not_eligible() -> None:
    eligibility = commander_eligibility(
        "The Eternity Elevator", "Legendary Artifact — Spacecraft", "", None, None
    )

    assert not eligibility.eligible
    assert eligibility.rule == "903.3"
    assert "power/toughness" in eligibility.reason


def test_a_planeswalker_that_says_it_can_be_your_commander_is_eligible() -> None:
    eligibility = commander_eligibility(
        "Teferi, Temporal Archmage", "Legendary Planeswalker — Teferi", TEFERI_TEXT, None, None
    )

    assert eligibility.eligible
    assert eligibility.rule == "903.3a"


@pytest.mark.parametrize(
    ("name", "type_line", "power", "toughness"),
    [
        ("Jace, the Mind Sculptor", "Legendary Planeswalker — Jace", None, None),
        ("Aetherworks Marvel", "Legendary Artifact", None, None),
        (
            "Delver of Secrets // Insectile Aberration",
            "Creature — Human Wizard // Creature — Human Insect",
            "1",
            "1",
        ),
        # A legendary creature only on its back face: outside the game, a card has
        # its front face's characteristics.
        (
            "Elbrus, the Binding Blade // Withengar Unbound",
            "Legendary Artifact — Equipment // Legendary Creature — Demon",
            None,
            None,
        ),
    ],
)
def test_other_cards_are_not_eligible(
    name: str, type_line: str, power: str | None, toughness: str | None
) -> None:
    eligibility = commander_eligibility(name, type_line, "", power, toughness)

    assert not eligibility.eligible
    assert eligibility.rule == "903.3"
    assert eligibility.reason


def test_can_be_your_commander_text_about_another_card_does_not_count() -> None:
    eligibility = commander_eligibility(
        "Jace, the Mind Sculptor",
        "Legendary Planeswalker — Jace",
        "Teferi, Temporal Archmage can be your commander.",
        None,
        None,
    )

    assert not eligibility.eligible


# --- CardFacts -------------------------------------------------------------


def test_card_facts_derive_every_rule_input_from_the_card() -> None:
    facts = CardFacts.from_card(
        oracle_id=uuid.UUID("6ad8011d-3471-4369-9d68-b264cc027487"),
        name="Seven Dwarves",
        type_line="Creature — Dwarf",
        oracle_text=DWARVES_TEXT,
        color_identity=["R"],
        commander_legality="legal",
        power="2",
        toughness="2",
    )

    assert facts.color_identity == frozenset({"R"})
    assert facts.copy_limit == 7
    assert facts.is_basic_land is False
    assert facts.is_free_basic is False
    assert facts.commander_eligibility.eligible is False
