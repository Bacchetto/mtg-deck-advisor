"""Storing embeddings for semantic search, redoing only what's stale (RAG-1, RAG-2, ING-3).

Every Commander-legal card, and every rule (one chunk per rule, ADR 0010), is
embedded once. A stored embedding is redone only
when it's stale: the card's content hash has changed, or it came from another
model or text version. So the first run embeds everything (about 32,000 cards)
and a rerun embeds nothing. Each batch is committed as it's done, so an
interrupted run keeps its progress.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import psycopg
import structlog
from psycopg import sql
from pydantic import BaseModel

from mtg_deck_advisor.llm.ollama import Embedder
from mtg_deck_advisor.retrieval.text import (
    CARD_TEXT_VERSION,
    RULE_TEXT_VERSION,
    card_text,
    rule_text,
)

log = structlog.get_logger(__name__)

# The vector size in the schema (migration 0009).
DIMENSIONS = 1024
DEFAULT_BATCH_SIZE = 64


class EmbeddingReport(BaseModel):
    added: int
    updated: int
    unchanged: int


@dataclass(frozen=True)
class Source:
    """One thing to embed: its key, its content hash, and the text to embed."""

    key: Any
    content_hash: str
    text: str


def vector_literal(vector: Sequence[float]) -> str:
    """A vector in pgvector's text form, `[0.1,0.2,...]`, so no adapter package is needed."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def embed_cards(
    conn: psycopg.Connection, embedder: Embedder, *, batch_size: int = DEFAULT_BATCH_SIZE
) -> EmbeddingReport:
    """Embed every Commander-legal card whose stored embedding is missing or stale."""
    rows = conn.execute(
        """
        SELECT oracle_id, content_hash, name, type_line, oracle_text
        FROM cards WHERE removed_at IS NULL AND commander_legality = 'legal'
        ORDER BY name
        """
    ).fetchall()
    sources = [Source(key=row[0], content_hash=row[1], text=card_text(*row[2:])) for row in rows]
    return embed_sources(
        conn, embedder, "card_embeddings", "oracle_id", sources, CARD_TEXT_VERSION, batch_size
    )


def embed_rules(
    conn: psycopg.Connection, embedder: Embedder, *, batch_size: int = DEFAULT_BATCH_SIZE
) -> EmbeddingReport:
    """Embed every current rule whose stored embedding is missing or stale (ADR 0010)."""
    rows = conn.execute(
        "SELECT number, content_hash, text FROM rules WHERE removed_at IS NULL ORDER BY number"
    ).fetchall()
    sources = [Source(key=row[0], content_hash=row[1], text=rule_text(row[2])) for row in rows]
    return embed_sources(
        conn, embedder, "rule_embeddings", "number", sources, RULE_TEXT_VERSION, batch_size
    )


def embed_sources(
    conn: psycopg.Connection,
    embedder: Embedder,
    table: str,
    key_column: str,
    sources: Sequence[Source],
    text_version: str,
    batch_size: int,
) -> EmbeddingReport:
    names = {"table": sql.Identifier(table), "key": sql.Identifier(key_column)}
    stored = {
        row[0]: tuple(row[1:])
        for row in conn.execute(
            sql.SQL("SELECT {key}, content_hash, model, text_version FROM {table}").format(**names)
        ).fetchall()
    }
    conn.commit()  # end the read, so each batch below commits on its own

    current = {
        source.key: (source.content_hash, embedder.model, text_version) for source in sources
    }
    stale = [source for source in sources if stored.get(source.key) != current[source.key]]
    added = sum(1 for source in stale if source.key not in stored)

    upsert = sql.SQL(
        """
        INSERT INTO {table} ({key}, embedding, content_hash, model, text_version)
        VALUES (%s, %s::vector, %s, %s, %s)
        ON CONFLICT ({key}) DO UPDATE SET
            embedding = EXCLUDED.embedding,
            content_hash = EXCLUDED.content_hash,
            model = EXCLUDED.model,
            text_version = EXCLUDED.text_version,
            embedded_at = now()
        """
    ).format(**names)
    for start in range(0, len(stale), batch_size):
        batch = stale[start : start + batch_size]
        vectors = embedder.embed([source.text for source in batch])
        if wrong := next((len(v) for v in vectors if len(v) != DIMENSIONS), None):
            raise ValueError(
                f"{embedder.model} returned {wrong}-dimensional embeddings, but {table} holds "
                f"{DIMENSIONS}: a model with a different size needs a migration"
            )
        with conn.transaction():
            conn.cursor().executemany(
                upsert,
                [
                    (
                        source.key,
                        vector_literal(vector),
                        source.content_hash,
                        embedder.model,
                        text_version,
                    )
                    for source, vector in zip(batch, vectors, strict=True)
                ],
            )
        log.debug("embedding_progress", table=table, done=start + len(batch), total=len(stale))

    if stale:
        # Fresh statistics after a bulk load, so the planner's choices (such as
        # looking a pool's cards up by ID) rest on the real table sizes.
        conn.execute(sql.SQL("ANALYZE {table}").format(**names))
        conn.commit()

    report = EmbeddingReport(
        added=added, updated=len(stale) - added, unchanged=len(sources) - len(stale)
    )
    log.info("embedding_finished", table=table, model=embedder.model, **report.model_dump())
    return report
