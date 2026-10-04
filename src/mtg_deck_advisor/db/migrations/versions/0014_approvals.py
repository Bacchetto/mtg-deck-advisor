"""Approval records: a user's decision on a proposal or an export (GRD-2).

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-04
"""

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE approvals (
            id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            -- A decision on a proposal, or permission to export one deck version.
            kind         text NOT NULL CHECK (kind IN ('proposal', 'export')),
            proposal_id  uuid REFERENCES proposals (id),
            deck_id      uuid,
            deck_version integer,
            decision     text NOT NULL CHECK (decision IN ('approved', 'rejected')),
            -- Only a person decides. The agent can propose but never approve,
            -- and the database refuses it even if code tried.
            actor        text NOT NULL CHECK (actor = 'user'),
            note         text,
            trace_id     text,
            decided_at   timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (deck_id, deck_version) REFERENCES deck_versions (deck_id, version),
            CHECK (
                (kind = 'proposal' AND proposal_id IS NOT NULL)
                OR (kind = 'export' AND deck_id IS NOT NULL AND deck_version IS NOT NULL)
            )
        )
        """
    )
    # One decision per proposal: approved or rejected, once.
    op.execute(
        "CREATE UNIQUE INDEX approvals_one_per_proposal ON approvals (proposal_id) "
        "WHERE kind = 'proposal'"
    )
    op.execute("CREATE INDEX approvals_exports ON approvals (deck_id, deck_version)")
    op.execute(
        "CREATE TRIGGER approvals_immutable BEFORE UPDATE OR DELETE ON approvals "
        "FOR EACH ROW EXECUTE FUNCTION refuse_change('immutable')"
    )


def downgrade() -> None:
    op.execute("DROP TABLE approvals")
