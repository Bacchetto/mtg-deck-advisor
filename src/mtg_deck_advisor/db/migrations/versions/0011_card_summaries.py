"""One-sentence card summaries for pool searches, and their embeddings.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-04
"""

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE card_summaries (
            oracle_id      uuid PRIMARY KEY REFERENCES cards (oracle_id),
            -- What the card does, in the words a player would search with,
            -- written by a local model (retrieval.summaries).
            summary        text NOT NULL,
            -- What produced it. A card whose content hash has changed since, or
            -- a summary from an older prompt version, is stale and is redone.
            content_hash   text NOT NULL,
            prompt_version text NOT NULL,
            model          text NOT NULL,
            summarised_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # Same shape as card_embeddings (migration 0009), so the same code keeps
    # it up to date; its content hash is the hash of the summary text.
    op.execute(
        """
        CREATE TABLE summary_embeddings (
            oracle_id    uuid PRIMARY KEY REFERENCES cards (oracle_id),
            embedding    vector(1024) NOT NULL,
            content_hash text NOT NULL,
            model        text NOT NULL,
            text_version text NOT NULL,
            embedded_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX summary_embeddings_hnsw ON summary_embeddings "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE summary_embeddings")
    op.execute("DROP TABLE card_summaries")
