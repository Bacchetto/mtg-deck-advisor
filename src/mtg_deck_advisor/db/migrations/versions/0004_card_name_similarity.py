"""Trigram similarity on card names, for "did you mean" suggestions.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-01
"""

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # pg_trgm scores how alike two strings are by their shared three-letter
    # sequences, which is forgiving of typos ("Sol Rnig" is close to "Sol Ring").
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    # A GIN trigram index makes the similarity operator (%) an index lookup
    # instead of a comparison against every card.
    op.execute("CREATE INDEX cards_name_key_trgm_idx ON cards USING gin (name_key gin_trgm_ops)")


def downgrade() -> None:
    op.execute("DROP INDEX cards_name_key_trgm_idx")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
