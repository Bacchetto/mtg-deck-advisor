"""Card summaries for retrieval: written by a model, stored, embedded, and used by pool searches."""

import json
import re
import threading
from pathlib import Path
from uuid import UUID

import psycopg
import pytest

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.db.migrate import upgrade
from mtg_deck_advisor.ingestion.card_ingestion import apply_cards
from mtg_deck_advisor.ingestion.cards import normalise
from mtg_deck_advisor.llm.client import ModelClient
from mtg_deck_advisor.llm.fake import FakeEmbedder
from mtg_deck_advisor.llm.recording import MemoryRecorder
from mtg_deck_advisor.llm.types import ProviderRequest, ProviderResponse, Usage
from mtg_deck_advisor.retrieval.embeddings import embed_cards, embed_summaries
from mtg_deck_advisor.retrieval.search import CardFilters, search_cards
from mtg_deck_advisor.retrieval.summaries import SUMMARY_PROMPT_VERSION, summarise_cards

SAMPLE = Path(__file__).parent.parent / "fixtures" / "scryfall" / "oracle_cards_sample.jsonl"


class SummaryModel:
    """A fake model that summarises per card name, like a real one would.

    `skip` makes it leave one card out of any batch of more than one card,
    as qwen3:14b did with two vanilla cards in one batch.
    """

    def __init__(self, skip: str | None = None) -> None:
        self.skip = skip
        self.batches: list[list[str]] = []
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return "fake"

    @property
    def bills(self) -> bool:
        return False

    def complete(self, request: ProviderRequest, model: str) -> ProviderResponse:
        listing = request.messages[0].content
        names = [m[1] for m in re.finditer(r"^\d+\. (.+?) \|", listing, re.MULTILINE)]
        with self._lock:
            self.batches.append(names)
        cards = [
            {"index": i, "summary": f"A card called {name} that does useful things."}
            for i, name in enumerate(names, 1)
            if not (name == self.skip and len(names) > 1)
        ]
        return ProviderResponse(
            text=json.dumps({"cards": cards}),
            stop_reason="end_turn",
            usage=Usage(input_tokens=100, output_tokens=20 * len(names)),
            model=model,
        )

    @property
    def summarised(self) -> list[str]:
        return sorted(name for batch in self.batches for name in batch)


def client(model: SummaryModel) -> ModelClient:
    return ModelClient(model, "qwen3:14b", recorder=MemoryRecorder())


@pytest.fixture
def loaded(settings: Settings) -> Settings:
    upgrade(settings)
    lines = SAMPLE.read_text(encoding="utf-8").splitlines()
    with connect(settings) as conn:
        apply_cards(conn, [r for line in lines if (r := normalise(json.loads(line))) is not None])
        embed_cards(conn, FakeEmbedder())
    return settings


def legal_names(conn: psycopg.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM cards WHERE removed_at IS NULL AND commander_legality = 'legal'"
    ).fetchall()
    return sorted(row[0] for row in rows)


def test_every_legal_card_is_summarised_and_stored(loaded: Settings) -> None:
    model = SummaryModel()
    with connect(loaded) as conn:
        report = summarise_cards(conn, client(model), batch_size=2)
        stored = conn.execute(
            "SELECT c.name, s.summary, s.prompt_version, s.model, s.content_hash = c.content_hash "
            "FROM card_summaries s JOIN cards c USING (oracle_id)"
        ).fetchall()
        names = legal_names(conn)

    assert (report.added, report.updated, report.unchanged, report.failed) == (len(names), 0, 0, 0)
    assert sorted(row[0] for row in stored) == names == model.summarised
    assert {(row[2], row[3], row[4]) for row in stored} == {
        (SUMMARY_PROMPT_VERSION, "qwen3:14b", True)
    }
    assert all(row[1].startswith(f"A card called {row[0]}") for row in stored)


def test_a_second_run_summarises_nothing(loaded: Settings) -> None:
    with connect(loaded) as conn:
        summarise_cards(conn, client(SummaryModel()))
        again = SummaryModel()
        report = summarise_cards(conn, client(again))

    assert again.batches == []
    assert report.added == report.updated == 0


def test_only_a_card_whose_text_changed_is_summarised_again(loaded: Settings) -> None:
    with connect(loaded) as conn:
        summarise_cards(conn, client(SummaryModel()))
        conn.execute("UPDATE cards SET content_hash = 'errata' WHERE name = 'Sol Ring'")
        conn.commit()
        again = SummaryModel()
        report = summarise_cards(conn, client(again))

    assert again.summarised == ["Sol Ring"]
    assert report.updated == 1


def test_a_batch_missing_a_card_is_retried_card_by_card(loaded: Settings) -> None:
    model = SummaryModel(skip="Sol Ring")
    with connect(loaded) as conn:
        report = summarise_cards(conn, client(model), batch_size=25)
        names = legal_names(conn)
        stored = conn.execute("SELECT count(*) FROM card_summaries").fetchone()

    assert report.failed == 0
    assert stored == (len(names),)
    assert ["Sol Ring"] in model.batches  # retried on its own


def test_summaries_are_embedded_and_a_rerun_embeds_nothing(loaded: Settings) -> None:
    with connect(loaded) as conn:
        summarise_cards(conn, client(SummaryModel()))
        first = embed_summaries(conn, FakeEmbedder())
        again = FakeEmbedder()
        second = embed_summaries(conn, again)
        row = conn.execute(
            "SELECT s.summary, e.embedding::text FROM summary_embeddings e "
            "JOIN card_summaries s USING (oracle_id) "
            "JOIN cards c USING (oracle_id) WHERE c.name = 'Sol Ring'"
        ).fetchone()

    assert first.added > 0 and again.calls == [] and second.unchanged == first.added
    assert row is not None
    (expected,) = FakeEmbedder().embed([row[0]])
    assert json.loads(row[1]) == pytest.approx(expected, abs=1e-6)


def test_a_changed_summary_is_embedded_again(loaded: Settings) -> None:
    with connect(loaded) as conn:
        summarise_cards(conn, client(SummaryModel()))
        embed_summaries(conn, FakeEmbedder())
        conn.execute(
            "UPDATE card_summaries SET summary = 'Ramp: taps for two colorless mana.' "
            "WHERE oracle_id = (SELECT oracle_id FROM cards WHERE name = 'Sol Ring')"
        )
        conn.commit()
        again = FakeEmbedder()
        report = embed_summaries(conn, again)

    assert again.calls == [["Ramp: taps for two colorless mana."]]
    assert report.updated == 1


def pool_ids(conn: psycopg.Connection) -> list[UUID]:
    rows = conn.execute(
        "SELECT oracle_id FROM cards WHERE removed_at IS NULL AND commander_legality = 'legal'"
    ).fetchall()
    return [row[0] for row in rows]


def test_a_pool_search_finds_a_card_through_its_summary(loaded: Settings) -> None:
    with connect(loaded) as conn:
        # Sol Ring is the only card with a summary, so only it gets a score from
        # the summary arm on top of its vector rank: it must come first.
        conn.execute(
            "INSERT INTO card_summaries (oracle_id, summary, content_hash, prompt_version, model) "
            "SELECT oracle_id, 'xyzzy plugh, the rarest of effects.', content_hash, %s, 'fake' "
            "FROM cards WHERE name = 'Sol Ring'",
            (SUMMARY_PROMPT_VERSION,),
        )
        conn.commit()
        embed_summaries(conn, FakeEmbedder())
        pool = CardFilters(oracle_ids=pool_ids(conn))
        hits = search_cards(conn, FakeEmbedder(), "xyzzy plugh", pool, k=1)
        catalogue = search_cards(conn, FakeEmbedder(), "xyzzy plugh", k=10)

    assert [hit.name for hit in hits] == ["Sol Ring"]
    # A catalogue search doesn't use summaries: Sol Ring scores from one arm only.
    sol_ring = next(hit for hit in catalogue if hit.name == "Sol Ring")
    assert sol_ring.score < hits[0].score


def test_a_pool_search_still_works_with_no_summaries(loaded: Settings) -> None:
    with connect(loaded) as conn:
        pool = CardFilters(oracle_ids=pool_ids(conn))
        hits = search_cards(conn, FakeEmbedder(), "add colorless mana", pool, k=3)

    assert hits[0].name == "Sol Ring"


def test_summary_embeddings_have_a_cosine_hnsw_index(loaded: Settings) -> None:
    with connect(loaded) as conn:
        definitions = [
            row[0]
            for row in conn.execute(
                "SELECT indexdef FROM pg_indexes WHERE tablename = 'summary_embeddings'"
            ).fetchall()
        ]

    assert any("hnsw" in d and "vector_cosine_ops" in d for d in definitions)
