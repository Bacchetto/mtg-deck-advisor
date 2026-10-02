"""Embedding cards for semantic search, against a real pgvector database and a fake embedder."""

import json
from pathlib import Path

import pytest

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.db.migrate import upgrade
from mtg_deck_advisor.ingestion.card_ingestion import apply_cards
from mtg_deck_advisor.ingestion.cards import normalise
from mtg_deck_advisor.llm.fake import FakeEmbedder
from mtg_deck_advisor.retrieval.embeddings import embed_cards
from mtg_deck_advisor.retrieval.text import card_text

SAMPLE = Path(__file__).parent.parent / "fixtures" / "scryfall" / "oracle_cards_sample.jsonl"
# The sample's Commander-legal cards. Mox Jet is banned; Tyranid, the blue-red
# Red Herring and Brightglass Gearhulk aren't legal.
LEGAL = {
    "Lim-Dûl's Cohort",
    "Sol Ring",
    "Bonecrusher Giant // Stomp",
    "Delver of Secrets // Insectile Aberration",
}


@pytest.fixture
def loaded(settings: Settings) -> Settings:
    upgrade(settings)
    lines = SAMPLE.read_text(encoding="utf-8").splitlines()
    with connect(settings) as conn:
        apply_cards(conn, [r for line in lines if (r := normalise(json.loads(line))) is not None])
    return settings


def embedded_names(embedder: FakeEmbedder) -> list[str]:
    """The card names in the texts an embedder was given (each text starts "Name. Type.")."""
    return sorted(text.split(". ")[0] for call in embedder.calls for text in call)


def legal_count(settings: Settings) -> int:
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT count(*) FROM cards WHERE removed_at IS NULL AND commander_legality = 'legal'"
        ).fetchone()
    assert row is not None
    return int(row[0])


def test_every_commander_legal_card_is_embedded(loaded: Settings) -> None:
    embedder = FakeEmbedder()

    with connect(loaded) as conn:
        report = embed_cards(conn, embedder, batch_size=2)
        stored = conn.execute(
            "SELECT c.name, e.model, vector_dims(e.embedding) FROM card_embeddings e "
            "JOIN cards c USING (oracle_id)"
        ).fetchall()

    count = legal_count(loaded)
    assert (report.added, report.updated, report.unchanged) == (count, 0, 0)
    assert {name for name, _, _ in stored} >= LEGAL and len(stored) == count
    assert {(model, dims) for _, model, dims in stored} == {("fake-embedder", 1024)}


def test_the_stored_vector_is_the_embedding_of_the_cards_text(loaded: Settings) -> None:
    embedder = FakeEmbedder()

    with connect(loaded) as conn:
        embed_cards(conn, embedder)
        row = conn.execute(
            "SELECT c.name, c.type_line, c.oracle_text, e.embedding::text "
            "FROM card_embeddings e JOIN cards c USING (oracle_id) WHERE c.name = 'Sol Ring'"
        ).fetchone()

    assert row is not None
    (expected,) = FakeEmbedder().embed([card_text(row[0], row[1], row[2])])
    assert json.loads(row[3]) == pytest.approx(expected, abs=1e-6)


def test_a_second_run_embeds_nothing(loaded: Settings) -> None:
    with connect(loaded) as conn:
        embed_cards(conn, FakeEmbedder())
        again = FakeEmbedder()
        report = embed_cards(conn, again)

    assert again.calls == []
    assert (report.added, report.updated, report.unchanged) == (0, 0, legal_count(loaded))


def test_only_a_card_whose_text_changed_is_embedded_again(loaded: Settings) -> None:
    with connect(loaded) as conn:
        embed_cards(conn, FakeEmbedder())
        conn.execute("UPDATE cards SET content_hash = 'errata' WHERE name = 'Sol Ring'")
        conn.commit()
        again = FakeEmbedder()
        report = embed_cards(conn, again)

    assert embedded_names(again) == ["Sol Ring"]
    assert report.updated == 1


def test_a_different_model_embeds_everything_again(loaded: Settings) -> None:
    with connect(loaded) as conn:
        embed_cards(conn, FakeEmbedder())
        report = embed_cards(conn, FakeEmbedder(model="fake-2"))
        models = conn.execute("SELECT DISTINCT model FROM card_embeddings").fetchall()

    assert report.updated == legal_count(loaded)
    assert models == [("fake-2",)]


def test_embeddings_from_an_older_text_version_are_redone(loaded: Settings) -> None:
    with connect(loaded) as conn:
        embed_cards(conn, FakeEmbedder())
        conn.execute(
            "UPDATE card_embeddings SET text_version = 'cards-v0' WHERE oracle_id = "
            "(SELECT oracle_id FROM cards WHERE name = 'Lim-Dûl''s Cohort')"
        )
        conn.commit()
        again = FakeEmbedder()
        embed_cards(conn, again)

    assert embedded_names(again) == ["Lim-Dûl's Cohort"]


def test_removed_and_illegal_cards_are_not_embedded(loaded: Settings) -> None:
    with connect(loaded) as conn:
        conn.execute("UPDATE cards SET removed_at = now() WHERE name = 'Sol Ring'")
        conn.commit()
        embedder = FakeEmbedder()
        embed_cards(conn, embedder)

    names = embedded_names(embedder)
    assert "Sol Ring" not in names
    assert "Mox Jet" not in names and "Tyranid" not in names


def test_an_embedding_of_the_wrong_size_is_refused(loaded: Settings) -> None:
    with connect(loaded) as conn, pytest.raises(ValueError, match="1024"):
        embed_cards(conn, FakeEmbedder(dimensions=768))


def test_card_embeddings_have_a_cosine_hnsw_index(loaded: Settings) -> None:
    with connect(loaded) as conn:
        definitions = [
            row[0]
            for row in conn.execute(
                "SELECT indexdef FROM pg_indexes WHERE tablename IN "
                "('card_embeddings', 'rule_embeddings')"
            ).fetchall()
        ]

    hnsw = [d for d in definitions if "hnsw" in d and "vector_cosine_ops" in d]
    assert len(hnsw) == 2
