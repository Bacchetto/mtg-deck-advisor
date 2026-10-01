"""Cards, and a record of every ingestion run.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-01
"""

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE cards (
            -- Scryfall's oracle ID: one per card, shared by all its printings.
            oracle_id          uuid PRIMARY KEY,
            -- Not unique: a few joke cards share a name with each other or
            -- with a real card.
            name               text NOT NULL,
            -- The folded name used for lookups (lowercase, no accents),
            -- computed in Python so code and queries always agree.
            name_key           text NOT NULL,
            -- The folded front-face name of a multi-face card, so
            -- "Delver of Secrets" finds "Delver of Secrets // Insectile Aberration".
            front_face_key     text,
            layout             text NOT NULL,
            mana_cost          text NOT NULL,
            cmc                double precision NOT NULL,
            type_line          text NOT NULL,
            oracle_text        text NOT NULL,
            colors             text[] NOT NULL,
            color_identity     text[] NOT NULL,
            keywords           text[] NOT NULL,
            produced_mana      text[] NOT NULL,
            commander_legality text NOT NULL
                CHECK (commander_legality IN ('legal', 'banned', 'not_legal')),
            game_changer       boolean NOT NULL,
            -- SHA-256 of the fields above; it changes only when the card does.
            content_hash       text NOT NULL,
            first_seen_at      timestamptz NOT NULL DEFAULT now(),
            updated_at         timestamptz NOT NULL DEFAULT now(),
            -- Set when the card disappears from Scryfall's data, cleared if it
            -- returns. Rows are never deleted, so references stay valid.
            removed_at         timestamptz
        )
        """
    )
    op.execute("CREATE INDEX cards_name_key_idx ON cards (name_key)")
    op.execute(
        "CREATE INDEX cards_front_face_key_idx ON cards (front_face_key) "
        "WHERE front_face_key IS NOT NULL"
    )

    op.execute(
        """
        CREATE TABLE ingestion_runs (
            id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            -- What was ingested, e.g. 'scryfall_oracle_cards'.
            source         text NOT NULL,
            -- Which version of it, e.g. the bulk file's updated_at.
            source_version text NOT NULL,
            trace_id       text,
            started_at     timestamptz NOT NULL,
            -- NULL until the run finishes; a run that failed stays NULL.
            finished_at    timestamptz,
            added          integer,
            updated        integer,
            unchanged      integer,
            removed        integer
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE ingestion_runs")
    op.execute("DROP TABLE cards")
