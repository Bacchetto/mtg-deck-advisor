"""Semantic search over cards and rules, with exact filters (RAG-1, RAG-2).

The query is embedded and compared with stored embeddings by cosine distance
(pgvector's `<=>`). Filters are plain SQL conditions, so they're exact: a
card outside the commander's colors never appears, however similar it is.

**HNSW and filters.** The approximate (HNSW) index finds the nearest 40
vectors (`hnsw.ef_search`), and only then does Postgres apply the WHERE
clause. A filter that matches few cards ("mono-blue", measured on the real
catalogue) leaves fewer than k results: 4 instead of 10. pgvector 0.8's
iterative scan keeps scanning the index until k rows pass the filter; its
`relaxed_order` mode can return them slightly out of order, so they're
re-sorted by exact distance afterwards. For a small set of cards, such as a
pool, the planner looks them up by ID and sorts them exactly instead.

Only Commander-legal, current cards are searched, and only embeddings from
the embedder's own model: vectors from two models aren't comparable.
"""

import re
from typing import Any, Literal, Self
from uuid import UUID

import psycopg
from psycopg import sql
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from mtg_deck_advisor.llm.ollama import Embedder
from mtg_deck_advisor.retrieval.embeddings import vector_literal
from mtg_deck_advisor.retrieval.text import query_text

COLORS = "WUBRG"
TYPE_WORD = re.compile(r"^[A-Za-z'-]+$")
DEFAULT_K = 10


class CardFilters(BaseModel):
    """Exact conditions a card must meet. Unset fields don't filter."""

    model_config = ConfigDict(frozen=True)

    # Color identity within these colors ("" means colorless only), as for a
    # commander's identity.
    color_identity_within: str | None = None
    # Words that must all appear in the type line, such as ["Artifact", "Creature"].
    types: list[str] = []
    mana_value_min: float | None = None
    mana_value_max: float | None = None
    # Only these cards: a user's pool.
    oracle_ids: list[UUID] | None = None

    @field_validator("color_identity_within")
    @classmethod
    def _identity(cls, value: str | None) -> str | None:
        if value is not None and (
            any(color not in COLORS for color in value) or len(set(value)) != len(value)
        ):
            raise ValueError(f"a color identity is distinct letters from {COLORS}, or ''")
        return value

    @field_validator("types")
    @classmethod
    def _types(cls, value: list[str]) -> list[str]:
        if bad := [word for word in value if not TYPE_WORD.match(word)]:
            raise ValueError(f"each type is one word, not {bad[0]!r}")
        return value

    @model_validator(mode="after")
    def _range(self) -> Self:
        low, high = self.mana_value_min, self.mana_value_max
        if low is not None and high is not None and low > high:
            raise ValueError("mana_value_min is above mana_value_max")
        return self


class CardHit(BaseModel):
    oracle_id: UUID
    name: str
    type_line: str
    mana_cost: str
    oracle_text: str
    # Cosine similarity to the query, from -1 to 1; higher is closer.
    similarity: float


class RuleHit(BaseModel):
    """One rule: the citable unit (ADR 0010)."""

    number: str
    section: str
    text: str
    similarity: float


def card_conditions(filters: CardFilters) -> tuple[sql.Composable, list[Any]]:
    """The SQL conditions on `cards c` for these filters, and their parameters."""
    conditions = [sql.SQL("c.removed_at IS NULL AND c.commander_legality = 'legal'")]
    params: list[Any] = []
    if filters.color_identity_within is not None:
        conditions.append(sql.SQL("c.color_identity <@ %s::text[]"))
        params.append(list(filters.color_identity_within))
    if filters.types:
        # Every word, matched against the type line's words.
        conditions.append(sql.SQL(r"%s::text[] <@ regexp_split_to_array(c.type_line, '\s+')"))
        params.append(filters.types)
    if filters.mana_value_min is not None:
        conditions.append(sql.SQL("c.cmc >= %s"))
        params.append(filters.mana_value_min)
    if filters.mana_value_max is not None:
        conditions.append(sql.SQL("c.cmc <= %s"))
        params.append(filters.mana_value_max)
    if filters.oracle_ids is not None:
        conditions.append(sql.SQL("c.oracle_id = ANY(%s)"))
        params.append(filters.oracle_ids)
    return sql.SQL(" AND ").join(conditions), params


def _embed_query(embedder: Embedder, kind: Literal["cards", "rules"], query: str) -> str:
    (vector,) = embedder.embed([query_text(embedder.model, kind, query)])
    return vector_literal(vector)


def search_cards(
    conn: psycopg.Connection,
    embedder: Embedder,
    query: str,
    filters: CardFilters | None = None,
    *,
    k: int = DEFAULT_K,
) -> list[CardHit]:
    """The k cards closest in meaning to the query, among those the filters allow."""
    conditions, params = card_conditions(filters or CardFilters())
    statement = sql.SQL(
        """
        WITH nearest AS MATERIALIZED (
            SELECT e.oracle_id, e.embedding <=> %s::vector AS distance
            FROM card_embeddings e JOIN cards c USING (oracle_id)
            WHERE e.model = %s AND {conditions}
            ORDER BY distance
            LIMIT %s
        )
        SELECT c.oracle_id, c.name, c.type_line, c.mana_cost, c.oracle_text, 1 - n.distance
        FROM nearest n JOIN cards c USING (oracle_id)
        ORDER BY n.distance
        """
    ).format(conditions=conditions)
    vector = _embed_query(embedder, "cards", query)
    with conn.transaction():
        conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        rows = conn.execute(statement, [vector, embedder.model, *params, k]).fetchall()
    return [
        CardHit(
            oracle_id=row[0],
            name=row[1],
            type_line=row[2],
            mana_cost=row[3],
            oracle_text=row[4],
            similarity=row[5],
        )
        for row in rows
    ]


def search_rules(
    conn: psycopg.Connection, embedder: Embedder, query: str, *, k: int = DEFAULT_K
) -> list[RuleHit]:
    """The k rules closest in meaning to the query."""
    vector = _embed_query(embedder, "rules", query)
    rows = conn.execute(
        """
        SELECT r.number, r.section, r.text, 1 - (e.embedding <=> %s::vector) AS similarity
        FROM rule_embeddings e JOIN rules r USING (number)
        WHERE e.model = %s AND r.removed_at IS NULL
        ORDER BY e.embedding <=> %s::vector
        LIMIT %s
        """,
        (vector, embedder.model, vector, k),
    ).fetchall()
    return [RuleHit(number=row[0], section=row[1], text=row[2], similarity=row[3]) for row in rows]
