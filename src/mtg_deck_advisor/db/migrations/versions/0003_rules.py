"""The Comprehensive Rules, one row per numbered rule.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-01
"""

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE rules (
            -- The rule's number as printed, without a trailing period:
            -- '903.4', '903.4a', '704.5aa'. What an answer cites.
            number        text PRIMARY KEY,
            -- The rule a lettered rule belongs to ('903.4' for '903.4a').
            parent        text,
            -- The section heading, such as '903. Commander'.
            section       text NOT NULL,
            -- The rule's text, with any examples that follow it.
            text          text NOT NULL,
            -- Same scheme as cards: hash, timestamps, soft delete. A rule that
            -- is renumbered in a new edition shows up as one removed and one
            -- added.
            content_hash  text NOT NULL,
            first_seen_at timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now(),
            removed_at    timestamptz
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE rules")
