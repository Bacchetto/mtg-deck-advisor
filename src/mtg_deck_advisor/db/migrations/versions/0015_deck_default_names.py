"""Whether a deck still has the name code gave it, rather than one the user chose (#110).

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-05
"""

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Existing decks keep their names: they were all given one explicitly.
    op.execute("ALTER TABLE decks ADD COLUMN default_name boolean NOT NULL DEFAULT false")


def downgrade() -> None:
    op.execute("ALTER TABLE decks DROP COLUMN default_name")
