"""Every model call: what was asked, what came back, what it cost.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-02
"""

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE model_calls (
            id                          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            -- Links the call to every other model call, tool call and log line
            -- of the same request (OBS-2).
            trace_id                    text,
            -- What the call was for, such as 'role_tagging'.
            purpose                     text NOT NULL,
            provider                    text NOT NULL,
            model                       text NOT NULL,
            -- 1 for a first try, 2 for a retry after invalid output.
            attempt                     integer NOT NULL,
            outcome                     text NOT NULL CHECK (outcome IN
                ('ok', 'error', 'refusal', 'invalid_output', 'budget_exceeded')),
            -- The full request as sent, so a call can be reconstructed.
            request                     jsonb NOT NULL,
            response_text               text,
            stop_reason                 text,
            input_tokens                integer NOT NULL DEFAULT 0,
            output_tokens               integer NOT NULL DEFAULT 0,
            cache_read_input_tokens     integer NOT NULL DEFAULT 0,
            cache_creation_input_tokens integer NOT NULL DEFAULT 0,
            cost_usd                    numeric(12, 6) NOT NULL DEFAULT 0,
            latency_ms                  double precision,
            error                       text,
            created_at                  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX model_calls_trace_id_idx ON model_calls (trace_id)")
    op.execute("CREATE INDEX model_calls_created_at_idx ON model_calls (created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE model_calls")
