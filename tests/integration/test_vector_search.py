"""Semantic search with exact filters, against real pgvector indexes and a fake embedder."""

import copy
import json
import uuid
from pathlib import Path
from typing import Any

import psycopg
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
from mtg_deck_advisor.retrieval.search import CardFilters, search_cards, search_rules

FIXTURES = Path(__file__).parent.parent / "fixtures"
CARD_FILES = ("oracle_cards_sample.jsonl", "oracle_cards_punctuation.jsonl")
KYTHEON = "Kytheon, Hero of Akros // Gideon, Battle-Forged"


def raw_cards() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for name in CARD_FILES
        for line in (FIXTURES / "scryfall" / name).read_text(encoding="utf-8").splitlines()
    ]


def load(conn: psycopg.Connection, raws: list[dict[str, Any]]) -> None:
    apply_cards(conn, [record for raw in raws if (record := normalise(raw)) is not None])


@pytest.fixture
def loaded(settings: Settings) -> Settings:
    upgrade(settings)
    rules = (FIXTURES / "rules" / "comprehensive_rules_excerpt.txt").read_text(encoding="utf-8")
    with connect(settings) as conn:
        load(conn, raw_cards())
        apply_rules(conn, parse_rules(rules).rules)
        embed_cards(conn, FakeEmbedder())
        embed_rules(conn, FakeEmbedder())
    return settings


def names(settings: Settings, query: str, filters: CardFilters | None = None) -> list[str]:
    with connect(settings) as conn:
        hits = search_cards(conn, FakeEmbedder(), query, filters or CardFilters())
    return [hit.name for hit in hits]


def test_the_closest_card_comes_first_with_its_text_and_score(loaded: Settings) -> None:
    with connect(loaded) as conn:
        hits = search_cards(conn, FakeEmbedder(), "add colorless mana", CardFilters(), k=3)

    assert hits[0].name == "Sol Ring"
    assert hits[0].oracle_text == "{T}: Add {C}{C}."
    assert hits[0].similarity >= hits[1].similarity >= hits[2].similarity
    assert len(hits) == 3


def test_color_identity_is_within_the_given_colors(loaded: Settings) -> None:
    found = names(loaded, "creature", CardFilters(color_identity_within="B"))

    assert set(found) == {"Lim-Dûl's Cohort", "Sol Ring"}  # black, and colorless


def test_an_empty_identity_means_colorless_only(loaded: Settings) -> None:
    assert names(loaded, "anything", CardFilters(color_identity_within="")) == ["Sol Ring"]


def test_every_type_must_be_in_the_type_line(loaded: Settings) -> None:
    found = names(loaded, "anything", CardFilters(types=["Artifact", "Creature"]))

    assert found == ["Red Herring"]


def test_mana_value_bounds_are_inclusive(loaded: Settings) -> None:
    found = names(loaded, "anything", CardFilters(mana_value_min=2, mana_value_max=2))

    assert set(found) == {"Red Herring", "Glimpse the Unthinkable"}


def test_a_pool_search_only_returns_pool_cards(loaded: Settings) -> None:
    with connect(loaded) as conn:
        pool = [
            row[0]
            for row in conn.execute(
                "SELECT oracle_id FROM cards WHERE name = ANY(%s)",
                (["Lim-Dûl's Cohort", KYTHEON],),
            ).fetchall()
        ]

    found = names(loaded, "add colorless mana", CardFilters(oracle_ids=pool))

    assert sorted(found) == [KYTHEON, "Lim-Dûl's Cohort"]


def test_removed_or_banned_cards_never_appear(loaded: Settings) -> None:
    with connect(loaded) as conn:
        conn.execute("UPDATE cards SET removed_at = now() WHERE name = 'Sol Ring'")
        conn.execute(
            "UPDATE cards SET commander_legality = 'banned' WHERE name = %s",
            ("Lim-Dûl's Cohort",),
        )
        conn.commit()

    found = names(loaded, "add colorless mana creature zombie")

    assert "Sol Ring" not in found and "Lim-Dûl's Cohort" not in found


def test_embeddings_from_another_model_are_not_mixed_in(loaded: Settings) -> None:
    with connect(loaded) as conn:
        hits = search_cards(conn, FakeEmbedder(model="other"), "mana", CardFilters())

    assert hits == []


def test_a_heavily_filtered_search_still_returns_k_results(loaded: Settings) -> None:
    """pgvector's HNSW scan finds its nearest 40 (ef_search), and then the filter applies.

    With a filter matching 2% of cards, that would leave about one result.
    The iterative scan keeps going until it has k.
    """
    sol_ring = next(raw for raw in raw_cards() if raw["name"] == "Sol Ring")
    synthetic = []
    for i in range(1500):
        raw = copy.deepcopy(sol_ring)
        raw["oracle_id"] = str(uuid.UUID(int=i + 1))
        raw["name"] = f"Test Relic {i}"
        raw["oracle_text"] = f"{{T}}: Add {{C}}. Relic number {i} glows."
        raw["color_identity"] = ["U"] if i % 50 == 0 else ["R"]
        synthetic.append(raw)
    with connect(loaded) as conn:
        load(conn, raw_cards() + synthetic)
        embed_cards(conn, FakeEmbedder())
        conn.execute("SET enable_seqscan = off")  # make the planner use the HNSW index
        filters = CardFilters(color_identity_within="U")
        hits = search_cards(conn, FakeEmbedder(), "add colorless mana", filters, k=10)
        plan = conn.execute(
            "EXPLAIN SELECT 1 FROM card_embeddings ORDER BY embedding <=> "
            "(SELECT embedding FROM card_embeddings LIMIT 1) LIMIT 10"
        ).fetchall()

    assert len(hits) == 10
    assert any("card_embeddings_hnsw" in row[0] for row in plan)


def test_rules_search_returns_citable_rules(loaded: Settings) -> None:
    with connect(loaded) as conn:
        hits = search_rules(conn, FakeEmbedder(), "Is reminder text part of color identity?", k=3)

    assert hits[0].number == "903.4c"
    assert hits[0].section == "903. Commander"
    assert hits[0].text.startswith("Reminder text is ignored")
    assert len(hits) == 3
