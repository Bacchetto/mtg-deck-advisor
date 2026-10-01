"""Card name keys with punctuation ignored, for names typed without it.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-01
"""

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Nullable because existing rows are filled by the next ingestion run, not
    # here: the keys are computed in Python (cards.loose_name), so that code
    # and queries always agree, and adding them changes every card's content
    # hash, so that run updates every card once. Until then, the
    # punctuation-free lookup simply finds nothing for those rows.
    op.execute("ALTER TABLE cards ADD COLUMN loose_name_key text")
    op.execute("ALTER TABLE cards ADD COLUMN loose_front_face_key text")
    op.execute("CREATE INDEX cards_loose_name_key_idx ON cards (loose_name_key)")
    op.execute(
        "CREATE INDEX cards_loose_front_face_key_idx ON cards (loose_front_face_key) "
        "WHERE loose_front_face_key IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE cards DROP COLUMN loose_front_face_key")
    op.execute("ALTER TABLE cards DROP COLUMN loose_name_key")
