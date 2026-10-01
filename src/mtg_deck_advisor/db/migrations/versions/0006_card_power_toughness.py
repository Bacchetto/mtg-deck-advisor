"""Card power and toughness, which rule 903.3 needs for Spacecraft commanders.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-01
"""

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Text, not numbers: "*", "1+*" and "2.5" all occur. NULL for cards with no
    # power/toughness on their front face. Existing rows are filled by the next
    # ingestion run: adding the fields changes every card's content hash, so
    # that run updates every card once.
    op.execute("ALTER TABLE cards ADD COLUMN power text")
    op.execute("ALTER TABLE cards ADD COLUMN toughness text")


def downgrade() -> None:
    op.execute("ALTER TABLE cards DROP COLUMN toughness")
    op.execute("ALTER TABLE cards DROP COLUMN power")
