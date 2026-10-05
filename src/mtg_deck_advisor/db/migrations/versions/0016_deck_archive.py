"""Archived decks: hidden from lists, with all of their history kept (#112).

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-05
"""

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Archiving sets this and unarchiving clears it; nothing is ever deleted.
    op.execute("ALTER TABLE decks ADD COLUMN archived_at timestamptz")


def downgrade() -> None:
    op.execute("ALTER TABLE decks DROP COLUMN archived_at")
