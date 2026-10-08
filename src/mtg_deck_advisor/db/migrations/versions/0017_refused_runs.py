"""A refused run: the model declined to go on, apart from an error (#137).

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-08
"""

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent_runs DROP CONSTRAINT agent_runs_status_check")
    op.execute(
        "ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_status_check CHECK (status IN "
        "('running', 'completed', 'turn_limit', 'budget', 'refused', 'error'))"
    )


def downgrade() -> None:
    # Refused runs become errors, which is what they were recorded as before.
    op.execute("UPDATE agent_runs SET status = 'error' WHERE status = 'refused'")
    op.execute("ALTER TABLE agent_runs DROP CONSTRAINT agent_runs_status_check")
    op.execute(
        "ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_status_check CHECK (status IN "
        "('running', 'completed', 'turn_limit', 'budget', 'error'))"
    )
