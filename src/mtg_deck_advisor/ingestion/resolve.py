"""Turning parsed pool entries into cards (ING-1).

Names are compared in their folded form (`fold_name`: no case, accents or
apostrophe style), first against full card names, then against the front
faces of multi-face cards, so "Delver of Secrets" finds
"Delver of Secrets // Insectile Aberration". A few names belong to more than
one card, almost always joke cards sharing a name with each other or with a
real card; the single Commander-legal (or banned) candidate wins, and
otherwise the entry is reported as ambiguous rather than guessed. Names that
match nothing get "did you mean" suggestions by trigram similarity.

Every entry ends up in exactly one of matched, ambiguous or unknown.
"""

from collections import defaultdict
from collections.abc import Sequence
from typing import Literal
from uuid import UUID

import psycopg
from pydantic import BaseModel, ConfigDict

from mtg_deck_advisor.ingestion.cards import CommanderLegality, fold_name
from mtg_deck_advisor.ingestion.pool import PoolEntry

MAX_SUGGESTIONS = 3


class ResolvedCard(BaseModel):
    model_config = ConfigDict(frozen=True)

    oracle_id: UUID
    name: str
    commander_legality: CommanderLegality


class Matched(BaseModel):
    entry: PoolEntry
    card: ResolvedCard
    matched_by: Literal["name", "front_face"]


class Ambiguous(BaseModel):
    entry: PoolEntry
    candidates: list[ResolvedCard]


class Unknown(BaseModel):
    entry: PoolEntry
    suggestions: list[str]


class ResolvedPool(BaseModel):
    matched: list[Matched]
    ambiguous: list[Ambiguous]
    unknown: list[Unknown]


def _lookup_key(name: str) -> str:
    # Some exports write a multi-face name with one slash ("Fire / Ice");
    # Scryfall's names use two.
    return fold_name(name).replace(" / ", " // ")


def _candidates(
    conn: psycopg.Connection, column: Literal["name_key", "front_face_key"], keys: list[str]
) -> dict[str, list[ResolvedCard]]:
    """Cards whose `column` is one of `keys`, grouped by key. One query for all keys."""
    query = (
        "SELECT name_key, oracle_id, name, commander_legality FROM cards "
        "WHERE removed_at IS NULL AND name_key = ANY(%s) ORDER BY name, oracle_id"
        if column == "name_key"
        else "SELECT front_face_key, oracle_id, name, commander_legality FROM cards "
        "WHERE removed_at IS NULL AND front_face_key = ANY(%s) ORDER BY name, oracle_id"
    )
    found: dict[str, list[ResolvedCard]] = defaultdict(list)
    for key, oracle_id, name, legality in conn.execute(query, (keys,)):
        found[key].append(ResolvedCard(oracle_id=oracle_id, name=name, commander_legality=legality))
    return found


def _pick(candidates: list[ResolvedCard]) -> ResolvedCard | None:
    """The one card a name means, or None if it could be more than one."""
    if len(candidates) == 1:
        return candidates[0]
    playable = [c for c in candidates if c.commander_legality != "not_legal"]
    return playable[0] if len(playable) == 1 else None


def _suggestions(conn: psycopg.Connection, key: str) -> list[str]:
    # `%` is pg_trgm's similarity operator (above its default 0.3 threshold),
    # written `%%` because psycopg uses `%s` for parameters.
    rows = conn.execute(
        """
        SELECT name FROM cards
        WHERE removed_at IS NULL AND name_key %% %(key)s
        GROUP BY name
        ORDER BY max(similarity(name_key, %(key)s)) DESC, name
        LIMIT %(limit)s
        """,
        {"key": key, "limit": MAX_SUGGESTIONS},
    )
    return [name for (name,) in rows]


def resolve(conn: psycopg.Connection, entries: Sequence[PoolEntry]) -> ResolvedPool:
    """Resolve every entry, keeping the input order within each group."""
    keys = {entry.name: _lookup_key(entry.name) for entry in entries}
    by_name = _candidates(conn, "name_key", sorted(set(keys.values())))
    by_front = _candidates(
        conn, "front_face_key", sorted({key for key in keys.values() if key not in by_name})
    )

    pool = ResolvedPool(matched=[], ambiguous=[], unknown=[])
    suggestions: dict[str, list[str]] = {}
    for entry in entries:
        key = keys[entry.name]
        matched_by: Literal["name", "front_face"] = "name" if key in by_name else "front_face"
        candidates = by_name.get(key) or by_front.get(key)
        if not candidates:
            if key not in suggestions:
                suggestions[key] = _suggestions(conn, key)
            pool.unknown.append(Unknown(entry=entry, suggestions=suggestions[key]))
        elif card := _pick(candidates):
            pool.matched.append(Matched(entry=entry, card=card, matched_by=matched_by))
        else:
            pool.ambiguous.append(Ambiguous(entry=entry, candidates=candidates))
    return pool
