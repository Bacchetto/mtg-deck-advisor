"""The text embedded for each card: symbols written out as words (see ADR 0009)."""

import pytest

from mtg_deck_advisor.retrieval.text import card_text, expand_symbols


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("{T}: Add {C}{C}.", "tap: Add one colorless mana one colorless mana."),
        ("{2}{G}, {Q}: Draw a card.", "two generic mana one green mana, untap: Draw a card."),
        ("{12}{U}{U}", "12 generic mana one blue mana one blue mana"),
        ("{X}{R}", "X mana one red mana"),
        ("{G/U}", "one green or blue mana"),
        ("{2/W}", "two generic or one white mana"),
        ("{B/P}", "one black Phyrexian mana"),
        ("{R/G/P}", "one red or green Phyrexian mana"),
        ("{S}{E}", "one snow mana one energy"),
        ("{TK}{TK} — {T}: Add one mana", "TK TK — tap: Add one mana"),
        ("No symbols here.", "No symbols here."),
    ],
)
def test_symbols_are_written_out_as_words(text: str, expected: str) -> None:
    assert expand_symbols(text) == expected


def test_a_card_is_its_name_type_cost_and_text() -> None:
    assert card_text("Sol Ring", "Artifact", "{1}", "{T}: Add {C}{C}.") == (
        "Sol Ring. Artifact. Costs one generic mana. "
        "tap: Add one colorless mana one colorless mana."
    )


def test_a_card_without_a_cost_or_text_leaves_them_out() -> None:
    assert card_text("Plains", "Basic Land — Plains", "", "") == "Plains. Basic Land — Plains."


def test_each_face_of_a_two_faced_card_has_its_cost() -> None:
    text = card_text(
        "Bellowing Bruiser // Beat a Path",
        "Creature — Ogre // Sorcery — Adventure",
        "{4}{R} // {2}{R}",
        "Haste\n//\nUp to two target creatures can't block this turn.",
    )

    assert "Costs four generic mana one red mana or two generic mana one red mana." in text
    assert text.endswith("Haste / // / Up to two target creatures can't block this turn.")


def test_a_back_face_without_a_cost_adds_nothing() -> None:
    text = card_text("Ajani // Ajani", "Creature // Planeswalker", "{1}{W} // ", "")

    assert "Costs one generic mana one white mana." in text
    assert " or " not in text
