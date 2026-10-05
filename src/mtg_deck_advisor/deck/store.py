"""Pools and decks in the database: the state the agent works on, owned by code (AGT-3).

A deck is a name and a pool plus numbered versions. Saving a DeckState adds the
next version; no version is ever changed (a trigger refuses it), so every
applied change can be traced and undone by going back a version.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from mtg_deck_advisor.deck.state import DeckState

PoolSource = Literal["text", "csv"]


@dataclass(frozen=True)
class Pool:
    id: UUID
    name: str
    source: PoolSource
    # The cards the user owns, with how many copies of each.
    cards: Mapping[UUID, int]


@dataclass(frozen=True)
class Deck:
    id: UUID
    pool_id: UUID
    name: str
    # None for a deck with no saved version yet.
    version: int | None
    state: DeckState | None


def create_pool(
    conn: psycopg.Connection, cards: Mapping[UUID, int], *, name: str, source: PoolSource
) -> UUID:
    with conn.transaction():
        row = conn.execute(
            "INSERT INTO pools (name, source) VALUES (%s, %s) RETURNING id", (name, source)
        ).fetchone()
        pool_id: UUID = _returned(row)
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO pool_cards (pool_id, oracle_id, count) VALUES (%s, %s, %s)",
                [(pool_id, card, count) for card, count in cards.items()],
            )
    return pool_id


def load_pool(conn: psycopg.Connection, pool_id: UUID) -> Pool | None:
    row = conn.execute("SELECT name, source FROM pools WHERE id = %s", (pool_id,)).fetchone()
    if row is None:
        return None
    cards = conn.execute(
        "SELECT oracle_id, count FROM pool_cards WHERE pool_id = %s", (pool_id,)
    ).fetchall()
    return Pool(id=pool_id, name=row[0], source=row[1], cards=dict(cards))


def create_deck(conn: psycopg.Connection, pool_id: UUID, *, name: str) -> UUID:
    row = conn.execute(
        "INSERT INTO decks (pool_id, name) VALUES (%s, %s) RETURNING id", (pool_id, name)
    ).fetchone()
    deck_id: UUID = _returned(row)
    return deck_id


def save_version(
    conn: psycopg.Connection,
    deck_id: UUID,
    state: DeckState,
    *,
    proposal_id: UUID | None = None,
) -> int:
    """Save `state` as the deck's next version, and return its number.

    Two saves racing for the same number can't both succeed: the version is
    part of the primary key, so the loser fails rather than overwriting.
    """
    row = conn.execute(
        """
        INSERT INTO deck_versions (deck_id, version, commander, cards, proposal_id)
        SELECT %(deck)s, COALESCE(MAX(version), 0) + 1, %(commander)s, %(cards)s, %(proposal)s
        FROM deck_versions WHERE deck_id = %(deck)s
        RETURNING version
        """,
        {
            "deck": deck_id,
            "commander": state.commander,
            "cards": Jsonb(state.to_dict()["cards"]),
            "proposal": proposal_id,
        },
    ).fetchone()
    version: int = _returned(row)
    return version


def load_deck(
    conn: psycopg.Connection, deck_id: UUID, *, version: int | None = None
) -> Deck | None:
    """The deck at `version`, or at its latest version. None if either doesn't exist."""
    deck = conn.execute("SELECT pool_id, name FROM decks WHERE id = %s", (deck_id,)).fetchone()
    if deck is None:
        return None
    row = conn.execute(
        """
        SELECT version, commander, cards FROM deck_versions
        WHERE deck_id = %s AND (%s::integer IS NULL OR version = %s)
        ORDER BY version DESC LIMIT 1
        """,
        (deck_id, version, version),
    ).fetchone()
    if row is None:
        if version is not None:
            return None
        return Deck(id=deck_id, pool_id=deck[0], name=deck[1], version=None, state=None)
    state = DeckState.from_dict({"commander": str(row[1]), "cards": row[2]})
    return Deck(id=deck_id, pool_id=deck[0], name=deck[1], version=row[0], state=state)


def decklist(conn: psycopg.Connection, state: DeckState) -> str:
    """A plain decklist: `1 Card Name` lines, the commander first, then by name."""
    names: dict[UUID, str] = dict(
        conn.execute(
            "SELECT oracle_id, name FROM cards WHERE oracle_id = ANY(%s)",
            ([state.commander, *state.cards],),
        ).fetchall()
    )
    others = sorted(state.cards.items(), key=lambda item: names[item[0]])
    lines = [f"1 {names[state.commander]}"] + [f"{count} {names[card]}" for card, count in others]
    return "\n".join(lines)


def _returned(row: tuple[Any, ...] | None) -> Any:
    """The value an INSERT ... RETURNING gave back (it always gives one row)."""
    if row is None:
        raise RuntimeError("INSERT ... RETURNING returned no row")
    return row[0]


@dataclass(frozen=True)
class PoolListing:
    id: UUID
    name: str
    source: PoolSource
    cards: int
    distinct: int
    created_at: datetime


def list_pools(conn: psycopg.Connection) -> list[PoolListing]:
    """Every pool, oldest first, with its card counts."""
    rows = conn.execute(
        """
        SELECT p.id, p.name, p.source, coalesce(sum(c.count), 0)::int, count(c.oracle_id)::int,
               p.created_at
        FROM pools p LEFT JOIN pool_cards c ON c.pool_id = p.id
        GROUP BY p.id ORDER BY p.created_at, p.id
        """
    ).fetchall()
    return [PoolListing(*row) for row in rows]


@dataclass(frozen=True)
class VersionListing:
    version: int
    proposal_id: UUID | None
    created_at: datetime


def deck_versions(conn: psycopg.Connection, deck_id: UUID) -> list[VersionListing]:
    """A deck's saved versions, oldest first, with the proposal each came from."""
    rows = conn.execute(
        "SELECT version, proposal_id, created_at FROM deck_versions WHERE deck_id = %s "
        "ORDER BY version",
        (deck_id,),
    ).fetchall()
    return [VersionListing(*row) for row in rows]


def card_names(conn: psycopg.Connection, oracle_ids: Iterable[UUID]) -> dict[UUID, str]:
    rows = conn.execute(
        "SELECT oracle_id, name FROM cards WHERE oracle_id = ANY(%s)", (list(oracle_ids),)
    ).fetchall()
    return dict(rows)
