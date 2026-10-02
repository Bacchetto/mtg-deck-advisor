"""Embeddings of cards and rules, for semantic search.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-02
"""

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | None = None
depends_on: str | None = None

# Separate tables rather than columns on cards and rules, so ingestion never
# needs a model, and embedding can be rerun on its own. The vector size is
# the embedding model's (qwen3-embedding:0.6b, ADR 0009); a model with a
# different size needs a migration, deliberately: mixing vectors from two
# models in one index would make similarities meaningless.
TABLES = {
    "card_embeddings": "oracle_id uuid PRIMARY KEY REFERENCES cards (oracle_id)",
    "rule_embeddings": "number text PRIMARY KEY REFERENCES rules (number)",
}


def upgrade() -> None:
    for table, key in TABLES.items():
        op.execute(
            f"""
            CREATE TABLE {table} (
                {key},
                embedding    vector(1024) NOT NULL,
                -- What produced the vector. A row whose source's content hash
                -- has changed since, or from another model or text version, is
                -- stale and is embedded again.
                content_hash text NOT NULL,
                model        text NOT NULL,
                text_version text NOT NULL,
                embedded_at  timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        # Approximate nearest-neighbour search by cosine distance (`<=>`).
        op.execute(f"CREATE INDEX {table}_hnsw ON {table} USING hnsw (embedding vector_cosine_ops)")


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP TABLE {table}")
