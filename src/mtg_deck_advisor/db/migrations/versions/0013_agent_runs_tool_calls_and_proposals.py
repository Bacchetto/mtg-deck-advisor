"""Agent runs, the tool calls they make, and the proposals they leave (AGT-2, OBS-1, GRD-1).

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-04
"""

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE agent_runs (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            task        text NOT NULL CHECK (task IN ('draft', 'refine', 'rules')),
            pool_id     uuid REFERENCES pools (id),
            deck_id     uuid REFERENCES decks (id),
            model       text NOT NULL,
            -- How the run ended. A run stopped by its turn limit or cost cap
            -- keeps what it had done as partial results (AGT-2).
            status      text NOT NULL CHECK (status IN
                ('running', 'completed', 'turn_limit', 'budget', 'error')),
            turns       integer NOT NULL DEFAULT 0,
            cost_usd    numeric(12, 6) NOT NULL DEFAULT 0,
            final_text  text,
            -- The conversation so far, saved when the run ends, however it ends.
            transcript  jsonb,
            error       text,
            -- Links the run to its model calls, tool calls and log lines (OBS-2).
            trace_id    text,
            started_at  timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz
        )
        """
    )
    op.execute(
        """
        CREATE TABLE tool_calls (
            id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            run_id       uuid NOT NULL REFERENCES agent_runs (id),
            turn         integer NOT NULL,
            -- The model's ID for the call, which ties it to its result.
            tool_call_id text NOT NULL,
            tool         text NOT NULL,
            arguments    jsonb NOT NULL,
            -- What went back to the model.
            result       text,
            -- 'error': unknown tool, bad arguments or a failure, returned to the
            -- model (AGT-4). 'rejected': a proposal that failed validation.
            outcome      text NOT NULL CHECK (outcome IN ('ok', 'error', 'rejected')),
            latency_ms   double precision,
            created_at   timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX tool_calls_run ON tool_calls (run_id, id)")
    op.execute(
        """
        CREATE TABLE proposals (
            id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id       uuid REFERENCES agent_runs (id),
            deck_id      uuid NOT NULL REFERENCES decks (id),
            -- A whole deck (propose_deck) or adds and removes (propose_changes).
            kind         text NOT NULL CHECK (kind IN ('deck', 'changes')),
            payload      jsonb NOT NULL,
            -- The deck version a change set was made against.
            base_version integer,
            rationale    text NOT NULL,
            citations    jsonb NOT NULL DEFAULT '[]',
            -- The validator's verdict: the violations, if any (GRD-1).
            validation   jsonb,
            status       text NOT NULL CHECK (status IN
                ('pending', 'invalid', 'approved', 'rejected', 'applied')),
            created_at   timestamptz NOT NULL DEFAULT now(),
            decided_at   timestamptz
        )
        """
    )
    # The proposal a version came from; none for a version saved directly.
    op.execute("ALTER TABLE deck_versions ADD COLUMN proposal_id uuid REFERENCES proposals (id)")


def downgrade() -> None:
    op.execute("ALTER TABLE deck_versions DROP COLUMN proposal_id")
    op.execute("DROP TABLE proposals")
    op.execute("DROP TABLE tool_calls")
    op.execute("DROP TABLE agent_runs")
