"""Checking the retrieval eval set's labels against the cards and rules in the database."""

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.db.migrate import upgrade
from mtg_deck_advisor.evaluation.retrieval_set import (
    CardQuery,
    QueryFilters,
    RuleQuestion,
    check_against_db,
)
from mtg_deck_advisor.ingestion.card_ingestion import apply_cards
from mtg_deck_advisor.ingestion.cards import normalise
from mtg_deck_advisor.ingestion.rules import parse_rules
from mtg_deck_advisor.ingestion.rules_ingestion import apply_rules

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def loaded(settings: Settings) -> Settings:
    upgrade(settings)
    lines = (FIXTURES / "scryfall" / "oracle_cards_sample.jsonl").read_text(encoding="utf-8")
    rules = (FIXTURES / "rules" / "comprehensive_rules_excerpt.txt").read_text(encoding="utf-8")
    with connect(settings) as conn:
        records = [normalise(json.loads(line)) for line in lines.splitlines()]
        apply_cards(conn, [record for record in records if record is not None])
        apply_rules(conn, parse_rules(rules).rules)
    return settings


def card_query(
    relevant: list[str], scope: str = "catalogue", filters: QueryFilters | None = None
) -> CardQuery:
    return CardQuery(
        id="C01",
        kind="need",
        scope=scope,
        query="q",
        filters=filters or QueryFilters(),
        relevant=relevant,
        notes="",
    )


def rule_question(relevant: list[str]) -> RuleQuestion:
    return RuleQuestion(id="R01", kind="commander", question="q", relevant=relevant, notes="")


def check(
    settings: Settings,
    cards: Sequence[CardQuery] = (),
    rules: Sequence[RuleQuestion] = (),
    pool: Sequence[str] = (),
) -> list[str]:
    with connect(settings) as conn:
        return check_against_db(conn, cards, rules, pool)


def test_labels_that_match_the_database_have_no_problems(loaded: Settings) -> None:
    problems = check(
        loaded,
        cards=[card_query(["Sol Ring"]), card_query(["Lim-Dûl's Cohort"], scope="pool")],
        rules=[rule_question(["903.4a"])],
        pool=["Sol Ring", "Lim-Dûl's Cohort"],
    )

    assert problems == []


def test_an_unknown_card_is_reported(loaded: Settings) -> None:
    (problem,) = check(loaded, cards=[card_query(["Sol Rnig"])])

    assert "C01" in problem and "Sol Rnig" in problem


def test_a_card_that_is_not_commander_legal_is_reported(loaded: Settings) -> None:
    (problem,) = check(loaded, cards=[card_query(["Mox Jet"])])

    assert "Mox Jet" in problem and "legal" in problem


def test_a_pool_query_can_only_label_cards_in_the_pool(loaded: Settings) -> None:
    (problem,) = check(
        loaded, cards=[card_query(["Sol Ring"], scope="pool")], pool=["Lim-Dûl's Cohort"]
    )

    assert "Sol Ring" in problem and "pool" in problem


def test_every_pool_card_must_exist(loaded: Settings) -> None:
    (problem,) = check(loaded, pool=["Sol Ring", "Not A Card"])

    assert "Not A Card" in problem


@pytest.mark.parametrize(
    ("card", "filters"),
    [
        ("Lim-Dûl's Cohort", QueryFilters(identity="R")),
        ("Sol Ring", QueryFilters(types=["Creature"])),
        ("Sol Ring", QueryFilters(mv_min=2)),
        ("Lim-Dûl's Cohort", QueryFilters(mv_max=2)),
    ],
)
def test_a_relevant_card_that_the_filters_exclude_is_reported(
    loaded: Settings, card: str, filters: QueryFilters
) -> None:
    (problem,) = check(loaded, cards=[card_query([card], filters=filters)])

    assert card in problem and "filter" in problem


def test_a_rule_number_that_does_not_exist_is_reported(loaded: Settings) -> None:
    (problem,) = check(loaded, rules=[rule_question(["903.4a", "999.9z"])])

    assert "R01" in problem and "999.9z" in problem
