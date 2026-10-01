"""Card ingestion against a real database, using real Scryfall entries as fixtures."""

import gzip
import json
from pathlib import Path
from typing import Any

import httpx2
import pytest

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.db.migrate import upgrade
from mtg_deck_advisor.ingestion.card_ingestion import apply_cards, ingest_cards, read_bulk_file
from mtg_deck_advisor.ingestion.cards import CardRecord, normalise
from mtg_deck_advisor.ingestion.scryfall import ScryfallClient

FIXTURE = Path(__file__).parent.parent / "fixtures" / "scryfall" / "oracle_cards_sample.jsonl"
SOL_RING = "6ad8011d-3471-4369-9d68-b264cc027487"


def raw_cards() -> list[dict[str, Any]]:
    """The fixture: 7 real cards, plus a token and an art card that are dropped."""
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]


def records(raws: list[dict[str, Any]]) -> list[CardRecord]:
    return [record for raw in raws if (record := normalise(raw)) is not None]


def by_id(raws: list[dict[str, Any]], oracle_id: str) -> dict[str, Any]:
    return next(raw for raw in raws if raw["oracle_id"] == oracle_id)


def row_versions(settings: Settings) -> dict[str, str]:
    """Each row's xmin, which changes whenever the row is rewritten, even with equal values."""
    with connect(settings) as conn:
        rows = conn.execute("SELECT oracle_id::text, xmin::text FROM cards").fetchall()
    return dict(rows)


@pytest.fixture
def migrated(settings: Settings) -> Settings:
    upgrade(settings)
    return settings


def apply(settings: Settings, raws: list[dict[str, Any]]) -> tuple[int, int, int, int]:
    with connect(settings) as conn:
        report = apply_cards(conn, records(raws))
    return report.added, report.updated, report.unchanged, report.removed


def test_the_first_run_adds_every_card(migrated: Settings) -> None:
    assert apply(migrated, raw_cards()) == (7, 0, 0, 0)

    with connect(migrated) as conn:
        names = {name for (name,) in conn.execute("SELECT name FROM cards").fetchall()}
    assert "Sol Ring" in names
    assert "Tyranid" not in names  # a token


def test_a_second_run_with_the_same_data_writes_nothing(migrated: Settings) -> None:
    apply(migrated, raw_cards())
    before = row_versions(migrated)

    assert apply(migrated, raw_cards()) == (0, 0, 7, 0)
    assert row_versions(migrated) == before


def test_a_price_or_image_change_is_not_an_update(migrated: Settings) -> None:
    apply(migrated, raw_cards())
    raws = raw_cards()
    by_id(raws, SOL_RING)["prices"] = {"usd": "999.99"}
    by_id(raws, SOL_RING)["image_uris"] = {"normal": "https://example.test/new.jpg"}

    assert apply(migrated, raws) == (0, 0, 7, 0)


def test_a_changed_card_is_updated(migrated: Settings) -> None:
    apply(migrated, raw_cards())
    raws = raw_cards()
    by_id(raws, SOL_RING)["oracle_text"] = "{T}: Add {C}{C}{C}."

    assert apply(migrated, raws) == (0, 1, 6, 0)
    with connect(migrated) as conn:
        row = conn.execute("SELECT oracle_text FROM cards WHERE oracle_id = %s", (SOL_RING,))
        assert row.fetchone() == ("{T}: Add {C}{C}{C}.",)


def test_a_missing_card_is_marked_removed_not_deleted(migrated: Settings) -> None:
    apply(migrated, raw_cards())
    without_sol_ring = [raw for raw in raw_cards() if raw["oracle_id"] != SOL_RING]

    assert apply(migrated, without_sol_ring) == (0, 0, 6, 1)
    with connect(migrated) as conn:
        row = conn.execute(
            "SELECT removed_at IS NOT NULL FROM cards WHERE oracle_id = %s", (SOL_RING,)
        ).fetchone()
    assert row == (True,)


def test_a_removed_card_that_returns_is_restored(migrated: Settings) -> None:
    apply(migrated, raw_cards())
    apply(migrated, [raw for raw in raw_cards() if raw["oracle_id"] != SOL_RING])

    assert apply(migrated, raw_cards()) == (1, 0, 6, 0)
    with connect(migrated) as conn:
        row = conn.execute(
            "SELECT removed_at IS NULL FROM cards WHERE oracle_id = %s", (SOL_RING,)
        ).fetchone()
    assert row == (True,)


def test_read_bulk_file_streams_the_gzipped_lines(tmp_path: Path) -> None:
    path = tmp_path / "cards.jsonl.gz"
    path.write_bytes(gzip.compress(FIXTURE.read_bytes()))

    assert [raw["name"] for raw in read_bulk_file(path)] == [raw["name"] for raw in raw_cards()]


def fake_scryfall(body: bytes) -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "data.scryfall.io":
            return httpx2.Response(200, stream=httpx2.ByteStream(body))
        return httpx2.Response(
            200,
            json={
                "type": "oracle_cards",
                "updated_at": "2026-10-01T09:01:55.977+00:00",
                "jsonl_download_uri": "https://data.scryfall.io/oracle-cards/oracle-cards-20261001090155.jsonl.gz",
                "compressed_size": len(body),
            },
        )

    return httpx2.MockTransport(handler)


def test_ingest_cards_runs_end_to_end_and_records_the_run(
    migrated: Settings, tmp_path: Path
) -> None:
    body = gzip.compress(FIXTURE.read_bytes())

    with ScryfallClient(
        user_agent="tests", cache_dir=tmp_path, transport=fake_scryfall(body), sleep=lambda s: None
    ) as client:
        first = ingest_cards(migrated, client)
        second = ingest_cards(migrated, client)

    assert (first.added, first.unchanged) == (7, 0)
    assert (second.added, second.updated, second.unchanged, second.removed) == (0, 0, 7, 0)

    with connect(migrated) as conn:
        runs = conn.execute(
            """
            SELECT source, source_version, added, updated, unchanged, removed,
                   finished_at IS NOT NULL, trace_id
            FROM ingestion_runs ORDER BY id
            """
        ).fetchall()
    assert [run[:7] for run in runs] == [
        ("scryfall_oracle_cards", "2026-10-01T09:01:55.977000+00:00", 7, 0, 0, 0, True),
        ("scryfall_oracle_cards", "2026-10-01T09:01:55.977000+00:00", 0, 0, 7, 0, True),
    ]
    assert all(len(run[7]) == 32 for run in runs)  # each run has its own trace ID
    assert runs[0][7] != runs[1][7]
