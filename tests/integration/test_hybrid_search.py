"""Keyword and hybrid search over cards and rules, against real Postgres full-text indexes."""

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.db.migrate import upgrade
from mtg_deck_advisor.ingestion.card_ingestion import apply_cards
from mtg_deck_advisor.ingestion.cards import normalise
from mtg_deck_advisor.ingestion.rules import parse_rules
from mtg_deck_advisor.ingestion.rules_ingestion import apply_rules
from mtg_deck_advisor.llm.fake import FakeEmbedder
from mtg_deck_advisor.retrieval.embeddings import embed_cards, embed_rules
from mtg_deck_advisor.retrieval.search import (
    CardFilters,
    SearchMode,
    search_cards,
    search_rules,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"
CARD_FILES = ("oracle_cards_sample.jsonl", "oracle_cards_punctuation.jsonl")
BONECRUSHER = "Bonecrusher Giant // Stomp"
COHORT = "Lim-Dûl's Cohort"


class FixedQueryEmbedder(FakeEmbedder):
    """Embeds every query as "add colorless mana", so vector search always favours mana rocks.

    That makes a card that only keyword search can find, as a real model
    misses exact terms it has no sense of.
    """

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return super().embed(["add colorless mana"] * len(texts))


@pytest.fixture
def loaded(settings: Settings) -> Settings:
    upgrade(settings)
    lines = [
        line
        for name in CARD_FILES
        for line in (FIXTURES / "scryfall" / name).read_text(encoding="utf-8").splitlines()
    ]
    rules = (FIXTURES / "rules" / "comprehensive_rules_excerpt.txt").read_text(encoding="utf-8")
    with connect(settings) as conn:
        apply_cards(conn, [r for line in lines if (r := normalise(json.loads(line))) is not None])
        apply_rules(conn, parse_rules(rules).rules)
        embed_cards(conn, FakeEmbedder())
        embed_rules(conn, FakeEmbedder())
    return settings


def names(
    settings: Settings,
    query: str,
    mode: SearchMode,
    filters: CardFilters | None = None,
    k: int = 10,
) -> list[str]:
    with connect(settings) as conn:
        hits = search_cards(conn, FixedQueryEmbedder(), query, filters, k=k, mode=mode)
    return [hit.name for hit in hits]


def test_keyword_search_finds_an_exact_word_in_the_rules_text(loaded: Settings) -> None:
    assert names(loaded, "regenerated", "keyword") == [COHORT]


def test_keyword_search_matches_word_forms(loaded: Settings) -> None:
    # "regenerating" and "regenerated" share a stem.
    assert names(loaded, "regenerating", "keyword") == [COHORT]


def test_a_card_matching_more_of_the_words_ranks_higher(loaded: Settings) -> None:
    # Both mention preventing damage; only Stomp says "prevented" and "target".
    found = names(loaded, "prevented damage target", "keyword")

    assert found[0] == BONECRUSHER


def test_names_and_type_lines_are_searchable(loaded: Settings) -> None:
    assert names(loaded, "insectile", "keyword") == ["Delver of Secrets // Insectile Aberration"]
    assert names(loaded, "fish", "keyword") == ["Red Herring"]


def test_a_query_of_only_common_words_finds_nothing_by_keyword(loaded: Settings) -> None:
    assert names(loaded, "the and of", "keyword") == []


def test_hybrid_surfaces_what_only_keyword_search_found(loaded: Settings) -> None:
    vector = names(loaded, "regenerated", "vector", k=2)
    hybrid = names(loaded, "regenerated", "hybrid", k=2)

    assert COHORT not in vector
    assert COHORT in hybrid
    assert vector[0] in hybrid  # and keeps the vector arm's best


def test_hybrid_is_the_default(loaded: Settings) -> None:
    with connect(loaded) as conn:
        hits = search_cards(conn, FixedQueryEmbedder(), "regenerated", k=2)

    assert COHORT in [hit.name for hit in hits]


def test_filters_apply_to_both_arms(loaded: Settings) -> None:
    blue = CardFilters(color_identity_within="U")

    assert names(loaded, "regenerated", "keyword", blue) == []
    assert COHORT not in names(loaded, "regenerated", "hybrid", blue)
    assert "Sol Ring" in names(loaded, "regenerated", "hybrid", blue)


def test_scores_are_in_descending_order_in_every_mode(loaded: Settings) -> None:
    with connect(loaded) as conn:
        modes: list[SearchMode] = ["vector", "keyword", "hybrid"]
        for mode in modes:
            hits = search_cards(conn, FixedQueryEmbedder(), "mana damage", mode=mode)
            scores = [hit.score for hit in hits]
            assert scores == sorted(scores, reverse=True), mode


def test_rules_keyword_search_finds_the_rule_with_the_word(loaded: Settings) -> None:
    with connect(loaded) as conn:
        hits = search_rules(conn, FixedQueryEmbedder(), "reminder", mode="keyword")

    assert [hit.number for hit in hits] == ["903.4c"]


def test_rules_hybrid_search_combines_both_arms(loaded: Settings) -> None:
    with connect(loaded) as conn:
        hits = search_rules(conn, FixedQueryEmbedder(), "reminder", k=3)

    assert "903.4c" in [hit.number for hit in hits]


def test_cards_and_rules_have_full_text_indexes(loaded: Settings) -> None:
    with connect(loaded) as conn:
        definitions = [
            row[0]
            for row in conn.execute(
                "SELECT indexdef FROM pg_indexes WHERE tablename IN ('cards', 'rules')"
            ).fetchall()
        ]

    assert len([d for d in definitions if "gin" in d and "search_vector" in d]) == 2
