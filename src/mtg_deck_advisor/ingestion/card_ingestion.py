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
from psycopg import sql

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.ingestion.cards import CardRecord, content_hash, normalise
from mtg_deck_advisor.ingestion.runs import finish_run, start_run
from mtg_deck_advisor.ingestion.scryfall import ScryfallClient
from mtg_deck_advisor.ingestion.sync import Existing, IngestionReport, diff
from mtg_deck_advisor.observability.tracing import traced

log = structlog.get_logger(__name__)

SOURCE = "scryfall_oracle_cards"

# The stored fields of a CardRecord, plus its hash, in table column order.
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
COLUMNS = (*CARD_FIELDS, "content_hash")


def read_bulk_file(path: Path) -> Iterator[dict[str, Any]]:
    """Each card in a gzipped JSON Lines bulk file, read one line at a time."""
    with gzip.open(path, "rt", encoding="utf-8") as lines:
        for line in lines:
            if line.strip():
                yield json.loads(line)


def _row(record: CardRecord, record_hash: str) -> tuple[Any, ...]:
    # Tuples become lists: psycopg sends a list as a Postgres array, but a
    # tuple as a composite value.
    values = [getattr(record, name) for name in CARD_FIELDS]
    return (*(list(v) if isinstance(v, tuple) else v for v in values), record_hash)


def apply_cards(conn: psycopg.Connection, records: Iterable[CardRecord]) -> IngestionReport:
    """Bring the cards table in line with `records`, in one transaction."""
    incoming: dict[UUID, tuple[CardRecord, str]] = {}
    for record in records:
        if record.oracle_id in incoming:
            raise ValueError(f"duplicate oracle_id in source data: {record.oracle_id}")
        incoming[record.oracle_id] = (record, content_hash(record))

    with conn.transaction():
        existing = {
            oracle_id: Existing(stored_hash, removed)
            for oracle_id, stored_hash, removed in conn.execute(
                "SELECT oracle_id, content_hash, removed_at IS NOT NULL FROM cards"
            )
        }
        changes = diff(existing, {key: record_hash for key, (_, record_hash) in incoming.items()})

        if changes.added:
            # COPY rather than INSERT: the first run loads ~35,000 rows.
            copy_sql = sql.SQL("COPY cards ({}) FROM STDIN").format(
                sql.SQL(", ").join(map(sql.Identifier, COLUMNS))
            )
            with conn.cursor().copy(copy_sql) as copy:
                for key in changes.added:
                    copy.write_row(_row(*incoming[key]))

        rewrite = changes.updated + changes.restored
        if rewrite:
            # Column names are composed as identifiers, never pasted into
            # the SQL text, so the statement is injection-safe by construction.
            update_sql = sql.SQL(
                "UPDATE cards SET {}, updated_at = now(), removed_at = NULL WHERE oracle_id = %s"
            ).format(
                sql.SQL(", ").join(
                    sql.SQL("{} = %s").format(sql.Identifier(column)) for column in COLUMNS[1:]
                )
            )
            conn.cursor().executemany(
                update_sql,
                [(*_row(*incoming[key])[1:], key) for key in rewrite],
            )

        if changes.removed:
            conn.execute(
                "UPDATE cards SET removed_at = now() WHERE oracle_id = ANY(%s)",
                (changes.removed,),
            )

    return changes.report()


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
