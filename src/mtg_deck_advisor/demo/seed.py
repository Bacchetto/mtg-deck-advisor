"""Seed an empty database with the demo dataset and the Demo collection (#139, DEM-1).

The dataset (`demo/data/`) holds the demo collection's cards with their
embeddings, summaries and role tags, plus every rule with its embedding, so
search returns what it returned when the demo was recorded. The collection
(`demo/collection/`) is the card lists and TCGplayer exports the Demo
collection pool is made from.

    python -m mtg_deck_advisor.demo.seed

Seeding twice does nothing the second time. A database that already has
cards, such as a fully ingested one, is left alone rather than mixed with
the dataset: `seed` raises, and the command says so and exits cleanly, so
`docker compose up` still starts a development stack.
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import psycopg

from mtg_deck_advisor.deck.store import create_pool, list_pools
from mtg_deck_advisor.evaluation.snapshot import load_snapshot
from mtg_deck_advisor.ingestion.pool import parse_csv, parse_text
from mtg_deck_advisor.ingestion.resolve import resolve

DATA_DIR = Path("demo/data")
COLLECTION_DIR = Path("demo/collection")
POOL_NAME = "Demo collection"


class AlreadyHasCardsError(RuntimeError):
    """The database has cards, but no Demo collection: not one to seed."""


@dataclass(frozen=True)
class SeedResult:
    # False when the database was already seeded.
    seeded: bool
    pool_id: UUID
    # Collection names that matched no single card: left out of the pool.
    unresolved: list[str]


def collection_cards(
    conn: psycopg.Connection, directory: Path = COLLECTION_DIR
) -> tuple[dict[UUID, int], list[str]]:
    """Every file's cards with their copies added up, and the names that didn't resolve."""
    counts: dict[UUID, int] = {}
    unresolved: list[str] = []
    for path in sorted(directory.iterdir()):
        text = path.read_text(encoding="utf-8-sig")
        parsed = parse_csv(text) if path.suffix.lower() == ".csv" else parse_text(text)
        resolved = resolve(conn, parsed.entries)
        for match in resolved.matched:
            oracle_id = match.card.oracle_id
            counts[oracle_id] = counts.get(oracle_id, 0) + match.entry.quantity
        unresolved += [item.entry.name for item in resolved.unknown]
        unresolved += [item.entry.name for item in resolved.ambiguous]
    return counts, list(dict.fromkeys(unresolved))


def seed(
    conn: psycopg.Connection, data: Path = DATA_DIR, collection: Path = COLLECTION_DIR
) -> SeedResult:
    """Load the dataset into an empty, migrated database and create the Demo collection."""
    existing = [pool for pool in list_pools(conn) if pool.name == POOL_NAME]
    if existing:
        return SeedResult(seeded=False, pool_id=existing[0].id, unresolved=[])
    if conn.execute("SELECT EXISTS (SELECT 1 FROM cards)").fetchone() == (True,):
        raise AlreadyHasCardsError(
            "the database already has cards, so it isn't seeded with the demo dataset; "
            "the demo needs its own, empty database"
        )
    load_snapshot(conn, data)
    cards, unresolved = collection_cards(conn, collection)
    pool_id = create_pool(conn, cards, name=POOL_NAME, source="csv")
    return SeedResult(seeded=True, pool_id=pool_id, unresolved=unresolved)


def main() -> int:
    from mtg_deck_advisor.config import get_settings
    from mtg_deck_advisor.db.connection import connect

    with connect(get_settings()) as conn:
        try:
            result = seed(conn)
        except AlreadyHasCardsError as exc:
            print(f"skipped: {exc}")
            return 0
        cards = conn.execute(
            "SELECT count(*), coalesce(sum(count), 0) FROM pool_cards WHERE pool_id = %s",
            (result.pool_id,),
        ).fetchone()
    if not result.seeded:
        print(f"already seeded: {POOL_NAME} is there")
        return 0
    distinct, total = cards or (0, 0)
    print(f"seeded: {POOL_NAME}, {total} cards ({distinct} distinct)")
    for name in result.unresolved:
        print(f"not in the dataset: {name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
