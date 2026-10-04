"""Pools, decks with immutable versions, and the append-only audit log (AGT-3, GRD-5).

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-04
"""

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Raised by triggers on rows that must never change once written. A trigger,
    # not a revoked privilege, so it holds for the table's owner too.
    op.execute(
        """
        CREATE FUNCTION refuse_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is %: % is not allowed', TG_TABLE_NAME, TG_ARGV[0], TG_OP;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TABLE pools (
            id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            name       text NOT NULL,
            -- How the user submitted it: a pasted list or a CSV export.
            source     text NOT NULL CHECK (source IN ('text', 'csv')),
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE pool_cards (
            pool_id   uuid NOT NULL REFERENCES pools (id) ON DELETE CASCADE,
            oracle_id uuid NOT NULL REFERENCES cards (oracle_id),
            -- How many copies the user owns.
            count     integer NOT NULL CHECK (count > 0),
            PRIMARY KEY (pool_id, oracle_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE decks (
            id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            pool_id    uuid NOT NULL REFERENCES pools (id),
            name       text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # Each applied change adds a version; none is ever edited, so any earlier
    # deck can be shown, compared or restored.
    op.execute(
        """
        CREATE TABLE deck_versions (
            deck_id    uuid NOT NULL REFERENCES decks (id),
            version    integer NOT NULL CHECK (version > 0),
            commander  uuid NOT NULL REFERENCES cards (oracle_id),
            -- The other cards, {oracle_id: count}, as DeckState.to_dict writes them.
            cards      jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (deck_id, version)
        )
        """
    )
    op.execute(
        "CREATE TRIGGER deck_versions_immutable BEFORE UPDATE OR DELETE ON deck_versions "
        "FOR EACH ROW EXECUTE FUNCTION refuse_change('immutable')"
    )
    # Every approval, rejection and executed action (GRD-5).
    op.execute(
        """
        CREATE TABLE audit_log (
            id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            at       timestamptz NOT NULL DEFAULT now(),
            actor    text NOT NULL CHECK (actor IN ('user', 'agent', 'system')),
            action   text NOT NULL,
            -- What was acted on, as 'kind:id', such as 'proposal:42'.
            subject  text NOT NULL,
            details  jsonb NOT NULL DEFAULT '{}',
            trace_id text
        )
        """
    )
    op.execute("CREATE INDEX audit_log_subject ON audit_log (subject, id)")
    op.execute(
        "CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION refuse_change('append-only')"
    )
    op.execute(
        "CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log "
        "FOR EACH STATEMENT EXECUTE FUNCTION refuse_change('append-only')"
    )


def downgrade() -> None:
    op.execute("DROP TABLE audit_log")
    op.execute("DROP TABLE deck_versions")
    op.execute("DROP TABLE decks")
    op.execute("DROP TABLE pool_cards")
    op.execute("DROP TABLE pools")
    op.execute("DROP FUNCTION refuse_change()")
