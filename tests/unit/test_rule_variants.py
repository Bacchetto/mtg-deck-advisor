"""Rules for other variants, kept out of Commander answers unless asked for (#138)."""

import pytest

from mtg_deck_advisor.retrieval.variants import excluded_variants, is_variant_rule


@pytest.mark.parametrize(
    "number",
    ["903.12", "903.12c", "903.13f", "805.8", "807.4i", "810.8c", "902.5b", "901.1", "905"],
)
def test_other_variants_rules_are_recognised(number: str) -> None:
    assert is_variant_rule(number)


@pytest.mark.parametrize(
    "number",
    # Commander itself, and the multiplayer rules a Commander game uses.
    ["903.3", "903.4c", "903.1", "903.120", "800.4a", "802.1", "806.1", "100.1", "704.6d"],
)
def test_commander_and_general_multiplayer_rules_are_not(number: str) -> None:
    assert not is_variant_rule(number)


def test_a_commander_question_excludes_every_other_variant() -> None:
    excluded = excluded_variants("Can a planeswalker be my commander?")

    assert "903.12" in excluded and "903.13" in excluded and "810" in excluded


@pytest.mark.parametrize(
    ("question", "named"),
    [
        ("Can a planeswalker be my commander in Brawl?", {"903.12"}),
        ("How big is a Commander Draft deck?", {"903.13"}),
        ("How does commander damage work in Two-Headed Giant?", {"810", "805"}),
        ("In ARCHENEMY, who goes first?", {"904", "805"}),
    ],
)
def test_a_variant_the_question_names_is_kept(question: str, named: set[str]) -> None:
    excluded = excluded_variants(question)

    assert not named & set(excluded)
    assert "901" in excluded  # Planechase isn't named, so it stays out
