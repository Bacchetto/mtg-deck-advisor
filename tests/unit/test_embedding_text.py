"""The text embedded for each card: symbols written out as words (see ADR 0009)."""

import pytest

from mtg_deck_advisor.retrieval.text import card_text, expand_symbols, query_text, rule_text


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


def test_a_card_is_its_name_type_and_text() -> None:
    assert card_text("Sol Ring", "Artifact", "{T}: Add {C}{C}.") == (
        "Sol Ring. Artifact. tap: Add one colorless mana one colorless mana."
    )


def test_a_card_without_text_is_its_name_and_type() -> None:
    assert card_text("Plains", "Basic Land — Plains", "") == "Plains. Basic Land — Plains."


def test_each_ability_and_face_stays_separated() -> None:
    text = card_text(
        "Bellowing Bruiser // Beat a Path",
        "Creature — Ogre // Sorcery — Adventure",
        "Haste\n//\nUp to two target creatures can't block this turn.",
    )

    assert text == (
        "Bellowing Bruiser // Beat a Path. Creature — Ogre // Sorcery — Adventure. "
        "Haste / // / Up to two target creatures can't block this turn."
    )


def test_a_top_level_rule_is_its_section_number_and_text() -> None:
    text = rule_text("903.8", "903. Commander", "A player may cast a commander they own.")

    assert text == "903. Commander. 903.8: A player may cast a commander they own."


def test_a_lettered_rule_is_introduced_by_its_parents_first_sentence() -> None:
    text = rule_text(
        "903.4c",
        "903. Commander",
        "Reminder text is ignored when determining a card's color identity. See rule 207.2.",
        parent=(
            "903.4",
            "The Commander variant uses color identity to determine what cards can be in a "
            "deck. The color identity of a card is the color or colors of any mana symbols "
            "(see rule 604.3).",
        ),
    )

    assert text == (
        "903. Commander. 903.4: The Commander variant uses color identity to determine what "
        "cards can be in a deck. 903.4c: Reminder text is ignored when determining a card's "
        "color identity. See rule 207.2."
    )


def test_a_rules_examples_stay_on_one_line() -> None:
    text = rule_text("100.1", "100. General", "A rule.\nExample: An example.")

    assert text == "100. General. 100.1: A rule. Example: An example."


def test_queries_to_a_qwen3_embedding_model_carry_its_instruction() -> None:
    text = query_text("qwen3-embedding:0.6b", "cards", "taps for two colorless mana")

    assert text.startswith("Instruct: ")
    assert text.endswith("\nQuery: taps for two colorless mana")
    assert query_text("qwen3-embedding:0.6b", "rules", "q") != query_text(
        "qwen3-embedding:0.6b", "cards", "q"
    )


def test_queries_to_other_models_are_sent_as_they_are() -> None:
    assert query_text("fake-embedder", "rules", "How big is a deck?") == "How big is a deck?"
