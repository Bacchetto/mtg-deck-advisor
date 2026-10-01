"""Parsing the Comprehensive Rules, using an excerpt of the real 2026-09-25 file."""

from datetime import date
from pathlib import Path

import pytest

from mtg_deck_advisor.ingestion.rules import parse_rules

EXCERPT = Path(__file__).parent.parent / "fixtures" / "rules" / "comprehensive_rules_excerpt.txt"


@pytest.fixture
def text() -> str:
    return EXCERPT.read_text(encoding="utf-8")


def test_the_effective_date_is_read_from_the_header(text: str) -> None:
    assert parse_rules(text).effective_date == date(2026, 9, 25)


def test_every_numbered_rule_is_found_in_order(text: str) -> None:
    numbers = [rule.number for rule in parse_rules(text).rules]

    assert numbers == [
        "100.1",
        "100.1a",
        "100.1b",
        "100.2",
        "704.5z",
        "704.5aa",
        "903.4",
        "903.4a",
        "903.4b",
        "903.4c",
    ]


def test_the_trailing_period_of_a_rule_number_is_not_part_of_it(text: str) -> None:
    rule = parse_rules(text).rules[0]

    assert rule.number == "100.1"
    assert rule.text.startswith("These Magic rules apply to any Magic game")


def test_lettered_rules_name_their_parent_rule(text: str) -> None:
    by_number = {rule.number: rule for rule in parse_rules(text).rules}

    assert by_number["903.4a"].parent == "903.4"
    assert by_number["704.5aa"].parent == "704.5"
    assert by_number["903.4"].parent is None


def test_each_rule_carries_its_section_heading(text: str) -> None:
    by_number = {rule.number: rule for rule in parse_rules(text).rules}

    assert by_number["100.1a"].section == "100. General"
    assert by_number["704.5aa"].section == "704. State-Based Actions"
    assert by_number["903.4c"].section == "903. Commander"


def test_an_example_belongs_to_the_rule_above_it(text: str) -> None:
    by_number = {rule.number: rule for rule in parse_rules(text).rules}

    assert "\nExample: Bosh, Iron Golem" in by_number["903.4"].text
    assert "Example" not in by_number["903.4a"].text


def test_the_contents_glossary_and_credits_are_not_rules(text: str) -> None:
    rules = parse_rules(text).rules
    all_text = "\n".join(rule.text for rule in rules)

    assert len(rules) == 10
    assert "Abandon" not in all_text  # first glossary entry
    assert "Richard Garfield" not in all_text  # credits


def test_non_breaking_space_lines_are_treated_as_blank(text: str) -> None:
    for rule in parse_rules(text).rules:
        assert "\xa0" not in rule.text
        assert rule.text == rule.text.strip()


def test_text_without_an_effective_date_is_rejected() -> None:
    with pytest.raises(ValueError, match="effective date"):
        parse_rules("100.1. A rule with no header above it.\n")


def test_text_with_no_rules_is_rejected() -> None:
    with pytest.raises(ValueError, match="no numbered rules"):
        parse_rules("These rules are effective as of September 25, 2026.\n\nGlossary\n")
