"""Loading Scryfall's oracle cards into the database, idempotently (ING-1, ING-3, ING-4).

Every card's content hash is compared with the stored one, and only what
differs is written: new cards inserted, changed cards updated, missing cards
marked removed (never deleted), returning cards restored. Unchanged cards are
not touched at all, so a second run with the same data writes nothing.
See ADR 0006.
"""

import gzip
import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import structlog

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.ingestion.cards import CardRecord, normalise
from mtg_deck_advisor.ingestion.runs import finish_run, start_run
from mtg_deck_advisor.ingestion.scryfall import ScryfallClient
from mtg_deck_advisor.ingestion.store import Row, apply_changes
from mtg_deck_advisor.ingestion.sync import IngestionReport, content_hash
from mtg_deck_advisor.observability.tracing import traced

log = structlog.get_logger(__name__)

SOURCE = "scryfall_oracle_cards"

# The stored fields of a CardRecord, in table column order (the key first).
CARD_FIELDS = (
    "oracle_id",
    "name",
    "name_key",
    "front_face_key",
    "layout",
    "mana_cost",
    "cmc",
    "type_line",
    "oracle_text",
    "colors",
    "color_identity",
    "keywords",
    "produced_mana",
    "commander_legality",
    "game_changer",
)


def read_bulk_file(path: Path) -> Iterator[dict[str, Any]]:
    """Each card in a gzipped JSON Lines bulk file, read one line at a time."""
    with gzip.open(path, "rt", encoding="utf-8") as lines:
        for line in lines:
            if line.strip():
                yield json.loads(line)


def _row(record: CardRecord) -> tuple[Any, ...]:
    # Tuples become lists: psycopg sends a list as a Postgres array, but a
    # tuple as a composite value.
    values = (getattr(record, name) for name in CARD_FIELDS)
    return tuple(list(v) if isinstance(v, tuple) else v for v in values)


def apply_cards(conn: psycopg.Connection, records: Iterable[CardRecord]) -> IngestionReport:
    """Bring the cards table in line with `records`, in one transaction."""
    incoming: dict[UUID, Row] = {}
    for record in records:
        if record.oracle_id in incoming:
            raise ValueError(f"duplicate oracle_id in source data: {record.oracle_id}")
        incoming[record.oracle_id] = (_row(record), content_hash(record))
    return apply_changes(conn, table="cards", columns=CARD_FIELDS, incoming=incoming)


def normalised_cards(path: Path) -> Iterator[CardRecord]:
    for raw in read_bulk_file(path):
        record = normalise(raw)
        if record is not None:
            yield record


def ingest_cards(settings: Settings, client: ScryfallClient) -> IngestionReport:
    """Fetch the current oracle cards file (through the cache) and apply it."""
    with traced() as trace_id:
        bulk = client.bulk_file("oracle_cards")
        path = client.download_bulk_file(bulk)
        source_version = bulk.updated_at.isoformat()
        with connect(settings) as conn:
            run_id = start_run(conn, SOURCE, source_version, trace_id)
            report = apply_cards(conn, normalised_cards(path))
            finish_run(conn, run_id, report)
        log.info("cards_ingested", source_version=source_version, **report.model_dump())
        return report
