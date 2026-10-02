"""Search over cards and rules: vector, keyword or hybrid, with exact filters (RAG-1, 2, 5).

- **Vector:** the query is embedded and compared with stored embeddings by
  cosine distance (pgvector's `<=>`). It finds meaning in other words ("taps
  for two colorless mana" for Sol Ring), but has no sense of exact terms.
- **Keyword:** Postgres full-text search over names, type lines and rules
  text (migration 0010). Any of the query's words may match, stemmed, and a
  card matching more of them ranks higher: `ts_rank_cd`, normalised by text
  length so long cards don't win by size. It finds exact terms ("ripple"),
  but not paraphrases.
- **Hybrid** (the default): each arm's top 50, fused by reciprocal rank
  (`fusion.py`). The retrieval eval measures whether it beats either arm.

Filters are plain SQL conditions, applied to every arm, so they're exact: a
card outside the commander's colors never appears, however good a match.

**HNSW and filters.** The approximate (HNSW) index finds the nearest 40
vectors (`hnsw.ef_search`), and only then does Postgres apply the WHERE
clause. A filter that matches few cards ("mono-blue", measured on the real
catalogue) leaves fewer than k results: 4 instead of 10. pgvector 0.8's
iterative scan keeps scanning the index until k rows pass the filter; its
`relaxed_order` mode can return them slightly out of order, so they're
re-sorted by exact distance afterwards.

The planner doesn't always use the index, and that's fine. For a small set
of cards, such as a pool, it looks them up by ID and sorts them exactly
(about 50 ms for 300 cards). For an unfiltered search of all 32,116 cards it
also prefers an exact scan (about 90 ms, measured), over the index (about
1 ms): slower, but exact, and well within an interactive budget.

Only Commander-legal, current cards are searched, and only embeddings from
the embedder's own model: vectors from two models aren't comparable.
"""

import re
from collections.abc import Hashable, Sequence
from typing import Any, Literal, Self
from uuid import UUID

import psycopg
from psycopg import sql
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from mtg_deck_advisor.llm.ollama import Embedder
from mtg_deck_advisor.retrieval.embeddings import vector_literal
from mtg_deck_advisor.retrieval.fusion import reciprocal_rank_fusion
from mtg_deck_advisor.retrieval.text import query_text

COLORS = "WUBRG"
TYPE_WORD = re.compile(r"^[A-Za-z'-]+$")
DEFAULT_K = 10
# How many results each arm contributes to hybrid search before fusion.
CANDIDATES = 50

type SearchMode = Literal["vector", "keyword", "hybrid"]

# Any of the query's words, stemmed. plainto_tsquery ANDs them ("artifact &
# tap & colorless"), which a sentence-long query would almost never match.
TSQUERY = sql.SQL("replace(plainto_tsquery('english', %s)::text, '&', '|')::tsquery")


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
    # Higher is better. The scale depends on the mode: cosine similarity
    # (vector), text rank (keyword), or fused reciprocal rank (hybrid).
    score: float


class RuleHit(BaseModel):
    """One rule: the citable unit (ADR 0010)."""

    number: str
    section: str
    text: str
    score: float  # as for CardHit


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


def _combine[K: Hashable](
    vector: Sequence[tuple[K, float]],
    keyword: Sequence[tuple[K, float]],
    mode: SearchMode,
    k: int,
) -> list[tuple[K, float]]:
    """The top k (key, score) pairs: one arm's own ranking, or both fused."""
    if mode == "hybrid":
        return reciprocal_rank_fusion([[key for key, _ in vector], [key for key, _ in keyword]])[:k]
    return list(vector if mode == "vector" else keyword)[:k]


def _card_vector_ranking(
    conn: psycopg.Connection,
    embedder: Embedder,
    query: str,
    conditions: sql.Composable,
    params: list[Any],
    limit: int,
) -> list[tuple[UUID, float]]:
    statement = sql.SQL(
        """
        WITH nearest AS MATERIALIZED (
            SELECT e.oracle_id, e.embedding <=> %s::vector AS distance
            FROM card_embeddings e JOIN cards c USING (oracle_id)
            WHERE e.model = %s AND {conditions}
            ORDER BY distance
            LIMIT %s
        )
        SELECT oracle_id, 1 - distance FROM nearest ORDER BY distance
        """
    ).format(conditions=conditions)
    vector = _embed_query(embedder, "cards", query)
    with conn.transaction():
        conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        rows = conn.execute(statement, [vector, embedder.model, *params, limit]).fetchall()
    return [(row[0], row[1]) for row in rows]


def _card_keyword_ranking(
    conn: psycopg.Connection,
    query: str,
    conditions: sql.Composable,
    params: list[Any],
    limit: int,
) -> list[tuple[UUID, float]]:
    statement = sql.SQL(
        """
        SELECT c.oracle_id, ts_rank_cd(c.search_vector, q.query, 1) AS rank
        FROM cards c, (SELECT {tsquery} AS query) q
        WHERE c.search_vector @@ q.query AND {conditions}
        ORDER BY rank DESC, c.name
        LIMIT %s
        """
    ).format(tsquery=TSQUERY, conditions=conditions)
    rows = conn.execute(statement, [query, *params, limit]).fetchall()
    return [(row[0], row[1]) for row in rows]


def search_cards(
    conn: psycopg.Connection,
    embedder: Embedder,
    query: str,
    filters: CardFilters | None = None,
    *,
    k: int = DEFAULT_K,
    mode: SearchMode = "hybrid",
) -> list[CardHit]:
    """The k best cards for the query, among those the filters allow."""
    conditions, params = card_conditions(filters or CardFilters())
    limit = CANDIDATES if mode == "hybrid" else k
    vector: list[tuple[UUID, float]] = []
    keyword: list[tuple[UUID, float]] = []
    if mode != "keyword":
        vector = _card_vector_ranking(conn, embedder, query, conditions, params, limit)
    if mode != "vector":
        keyword = _card_keyword_ranking(conn, query, conditions, params, limit)
    ranked = _combine(vector, keyword, mode, k)

    rows = conn.execute(
        "SELECT oracle_id, name, type_line, mana_cost, oracle_text FROM cards "
        "WHERE oracle_id = ANY(%s)",
        ([oracle_id for oracle_id, _ in ranked],),
    ).fetchall()
    cards = {row[0]: row for row in rows}
    return [
        CardHit(
            oracle_id=oracle_id,
            name=cards[oracle_id][1],
            type_line=cards[oracle_id][2],
            mana_cost=cards[oracle_id][3],
            oracle_text=cards[oracle_id][4],
            score=score,
        )
        for oracle_id, score in ranked
    ]


def search_rules(
    conn: psycopg.Connection,
    embedder: Embedder,
    query: str,
    *,
    k: int = DEFAULT_K,
    mode: SearchMode = "hybrid",
) -> list[RuleHit]:
    """The k best rules for the query."""
    limit = CANDIDATES if mode == "hybrid" else k
    vector: list[tuple[str, float]] = []
    keyword: list[tuple[str, float]] = []
    if mode != "keyword":
        embedded = _embed_query(embedder, "rules", query)
        rows = conn.execute(
            """
            SELECT e.number, 1 - (e.embedding <=> %s::vector)
            FROM rule_embeddings e JOIN rules r USING (number)
            WHERE e.model = %s AND r.removed_at IS NULL
            ORDER BY e.embedding <=> %s::vector
            LIMIT %s
            """,
            (embedded, embedder.model, embedded, limit),
        ).fetchall()
        vector = [(row[0], row[1]) for row in rows]
    if mode != "vector":
        statement = sql.SQL(
            """
            SELECT r.number, ts_rank_cd(r.search_vector, q.query, 1) AS rank
            FROM rules r, (SELECT {tsquery} AS query) q
            WHERE r.search_vector @@ q.query AND r.removed_at IS NULL
            ORDER BY rank DESC, r.number
            LIMIT %s
            """
        ).format(tsquery=TSQUERY)
        keyword = [(row[0], row[1]) for row in conn.execute(statement, [query, limit]).fetchall()]
    ranked = _combine(vector, keyword, mode, k)

    rows = conn.execute(
        "SELECT number, section, text FROM rules WHERE number = ANY(%s)",
        ([number for number, _ in ranked],),
    ).fetchall()
    rules = {row[0]: row for row in rows}
    return [
        RuleHit(number=number, section=rules[number][1], text=rules[number][2], score=score)
        for number, score in ranked
    ]
