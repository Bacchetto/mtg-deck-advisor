"""Turning parsed pool entries into cards (ING-1).

Names are compared in their folded form (`fold_name`: no case, accents or
apostrophe style), in four steps, each tried only for names the earlier steps
did not find:

1. the full card name
2. the front face of a multi-face card, so "Delver of Secrets" finds
   "Delver of Secrets // Insectile Aberration"
3. the full name with punctuation ignored (`loose_name`), so
   "Atraxa Praetors Voice" finds "Atraxa, Praetors' Voice"
4. the front face with punctuation ignored

An exact name therefore always wins over a looser one. A few names belong to more than
one card, almost always joke cards sharing a name with each other or with a
real card; the single Commander-legal (or banned) candidate wins, and
otherwise the entry is reported as ambiguous rather than guessed. Names that
match nothing get "did you mean" suggestions by trigram similarity.

Names still unfound are tried once more with trailing parenthesised tags
removed, as some exports add them ("Sol Ring (C18)", "Cosmic Rebirth
(Showcase)"). Only then, so a real name ending in parentheses ("Hazmat Suit
(Used)") always matches itself.

Every entry ends up in exactly one of matched, ambiguous or unknown.
"""

import re
from collections import defaultdict
from collections.abc import Sequence
from typing import Literal
from uuid import UUID

import psycopg
from psycopg import sql
from pydantic import BaseModel, ConfigDict

from mtg_deck_advisor.ingestion.cards import CommanderLegality, fold_name, loose_name
from mtg_deck_advisor.ingestion.pool import PoolEntry

MAX_SUGGESTIONS = 3
# One or more parenthesised tags at the end of a name.
TRAILING_TAGS = re.compile(r"(?:\s*\([^()]*\))+\s*$")


class ResolvedCard(BaseModel):
    model_config = ConfigDict(frozen=True)

    oracle_id: UUID
    name: str
    commander_legality: CommanderLegality


MatchedBy = Literal[
    "name", "front_face", "name_ignoring_punctuation", "front_face_ignoring_punctuation"
]

# Each lookup step: how a match is described, the column it searches, and
# whether it uses the punctuation-free key. Tried in this order.
STEPS: tuple[tuple[MatchedBy, str, bool], ...] = (
    ("name", "name_key", False),
    ("front_face", "front_face_key", False),
    ("name_ignoring_punctuation", "loose_name_key", True),
    ("front_face_ignoring_punctuation", "loose_front_face_key", True),
)


class Matched(BaseModel):
    entry: PoolEntry
    card: ResolvedCard
    matched_by: MatchedBy


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


def _lookup_keys(name: str) -> tuple[str, str]:
    """The exact and punctuation-free lookup keys for a typed name."""
    # Some exports write a multi-face name with one slash ("Fire / Ice");
    # Scryfall's names use two.
    exact = fold_name(name).replace(" / ", " // ")
    return exact, loose_name(exact)


def _candidates(
    conn: psycopg.Connection, column: str, keys: list[str]
) -> dict[str, list[ResolvedCard]]:
    """Cards whose `column` is one of `keys`, grouped by key. One query for all keys."""
    query = sql.SQL(
        "SELECT {column}, oracle_id, name, commander_legality FROM cards "
        "WHERE removed_at IS NULL AND {column} = ANY(%s) ORDER BY name, oracle_id"
    ).format(column=sql.Identifier(column))
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


def _find(
    conn: psycopg.Connection, names: dict[str, str]
) -> dict[str, tuple[MatchedBy, list[ResolvedCard]]]:
    """Run the steps in order for `names` (entry name to the name to look up),
    each step with one query for every name still unfound."""
    keys = {name: _lookup_keys(lookup) for name, lookup in names.items()}
    found: dict[str, tuple[MatchedBy, list[ResolvedCard]]] = {}
    for matched_by, column, loose in STEPS:
        step_key = {
            name: loose_key if loose else exact_key for name, (exact_key, loose_key) in keys.items()
        }
        pending = sorted({step_key[name] for name in keys if name not in found})
        if not pending:
            break
        step = _candidates(conn, column, pending)
        for name in keys:
            if name not in found and step_key[name] in step:
                found[name] = (matched_by, step[step_key[name]])
    return found


def resolve(conn: psycopg.Connection, entries: Sequence[PoolEntry]) -> ResolvedPool:
    """Resolve every entry, keeping the input order within each group."""
    keys = {entry.name: _lookup_keys(entry.name) for entry in entries}
    found = _find(conn, {name: name for name in keys})
    untagged = {
        name: stripped
        for name in keys
        if name not in found and (stripped := TRAILING_TAGS.sub("", name)) not in ("", name)
    }
    if untagged:
        found |= _find(conn, untagged)

    pool = ResolvedPool(matched=[], ambiguous=[], unknown=[])
    suggestions: dict[str, list[str]] = {}
    for entry in entries:
        if entry.name not in found:
            exact_key = keys[entry.name][0]
            if exact_key not in suggestions:
                suggestions[exact_key] = _suggestions(conn, exact_key)
            pool.unknown.append(Unknown(entry=entry, suggestions=suggestions[exact_key]))
            continue
        matched_by, candidates = found[entry.name]
        if card := _pick(candidates):
            pool.matched.append(Matched(entry=entry, card=card, matched_by=matched_by))
        else:
            pool.ambiguous.append(Ambiguous(entry=entry, candidates=candidates))
    return pool
