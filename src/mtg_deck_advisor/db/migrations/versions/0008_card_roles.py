"""Each card's roles in a Commander deck, as tagged by a model.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-02
"""

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE card_roles (
            oracle_id      uuid PRIMARY KEY REFERENCES cards (oracle_id),
            -- Role names from the taxonomy in llm.roles.
            roles          text[] NOT NULL,
            -- The model's one-sentence reason, for explaining a tag.
            reason         text NOT NULL,
            -- What produced the tags. A card whose content hash has changed since,
            -- or tags from an older prompt version, are stale and can be redone.
            content_hash   text NOT NULL,
            prompt_version text NOT NULL,
            model          text NOT NULL,
            tagged_at      timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE card_roles")
