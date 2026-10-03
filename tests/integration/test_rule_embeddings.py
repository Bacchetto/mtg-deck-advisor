"""Embedding the Comprehensive Rules as citable chunks, one rule each."""

import json
from pathlib import Path

import pytest

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.db.migrate import upgrade
from mtg_deck_advisor.ingestion.rules import parse_rules
from mtg_deck_advisor.ingestion.rules_ingestion import apply_rules
from mtg_deck_advisor.llm.fake import FakeEmbedder
from mtg_deck_advisor.retrieval.embeddings import embed_rules
from mtg_deck_advisor.retrieval.text import rule_text

EXCERPT = Path(__file__).parent.parent / "fixtures" / "rules" / "comprehensive_rules_excerpt.txt"


@pytest.fixture
def loaded(settings: Settings) -> Settings:
    upgrade(settings)
    with connect(settings) as conn:
        apply_rules(conn, parse_rules(EXCERPT.read_text(encoding="utf-8")).rules)
    return settings


def test_every_rule_is_embedded_and_a_rerun_embeds_nothing(loaded: Settings) -> None:
    with connect(loaded) as conn:
        first = embed_rules(conn, FakeEmbedder(), batch_size=3)
        again = FakeEmbedder()
        second = embed_rules(conn, again)
        stored = conn.execute("SELECT count(*) FROM rule_embeddings").fetchone()

    assert (first.added, first.updated, first.unchanged) == (10, 0, 0)
    assert (second.added, second.updated, second.unchanged) == (0, 0, 10)
    assert again.calls == []
    assert stored == (10,)


def test_a_rule_is_embedded_as_its_own_text(loaded: Settings) -> None:
    with connect(loaded) as conn:
        embed_rules(conn, FakeEmbedder())
        row = conn.execute(
            "SELECT r.text, e.embedding::text FROM rule_embeddings e JOIN rules r USING (number) "
            "WHERE number = '903.4c'"
        ).fetchone()

    assert row is not None
    (expected,) = FakeEmbedder().embed([rule_text(row[0])])
    assert json.loads(row[1]) == pytest.approx(expected, abs=1e-6)


def test_only_a_rule_whose_text_changed_is_embedded_again(loaded: Settings) -> None:
    with connect(loaded) as conn:
        embed_rules(conn, FakeEmbedder())
        # As ingestion would: new text, so a new content hash.
        conn.execute(
            "UPDATE rules SET text = 'Commander decks use color identity.', "
            "content_hash = 'edited' WHERE number = '903.4'"
        )
        conn.commit()
        again = FakeEmbedder()
        report = embed_rules(conn, again)

    assert again.calls == [["Commander decks use color identity."]]
    assert (report.updated, report.unchanged) == (1, 9)


def test_removed_rules_are_not_embedded(loaded: Settings) -> None:
    with connect(loaded) as conn:
        conn.execute("UPDATE rules SET removed_at = now() WHERE number = '100.2'")
        conn.commit()
        embedder = FakeEmbedder()
        report = embed_rules(conn, embedder)

    texts = [text for call in embedder.calls for text in call]
    assert report.added == 9
    assert not any("100.2:" in text for text in texts)
