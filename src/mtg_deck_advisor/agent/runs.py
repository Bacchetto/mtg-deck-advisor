"""Agent runs and the tool calls they make, in the database (AGT-2, OBS-1).

Every tool call is recorded with its arguments, what went back to the model,
its outcome and its latency, under the run and so under the run's trace ID,
next to the model calls in `model_calls`.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from mtg_deck_advisor.observability.tracing import current_trace_id

Task = Literal["draft", "refine", "rules"]
# "refused": the model declined to go on, which text in the pool or a card can cause (#137).
RunStatus = Literal["running", "completed", "turn_limit", "budget", "refused", "error"]
ToolOutcome = Literal["ok", "error", "rejected"]


@dataclass(frozen=True)
class RecordedToolCall:
    turn: int
    tool_call_id: str
    tool: str
    arguments: dict[str, Any]
    result: str | None
    outcome: ToolOutcome
    latency_ms: float | None
    created_at: datetime


def start_run(
    conn: psycopg.Connection,
    task: Task,
    model: str,
    *,
    pool_id: UUID | None = None,
    deck_id: UUID | None = None,
) -> UUID:
    """Record a run as started, under the current trace ID, and return its ID."""
    row = conn.execute(
        """
        INSERT INTO agent_runs (task, pool_id, deck_id, model, status, trace_id)
        VALUES (%s, %s, %s, %s, 'running', %s) RETURNING id
        """,
        (task, pool_id, deck_id, model, current_trace_id()),
    ).fetchone()
    if row is None:  # INSERT ... RETURNING always returns a row
        raise RuntimeError("agent run was not recorded")
    run_id: UUID = row[0]
    return run_id


def finish_run(
    conn: psycopg.Connection,
    run_id: UUID,
    *,
    status: RunStatus,
    turns: int,
    cost_usd: float,
    final_text: str | None,
    transcript: list[dict[str, Any]],
    error: str | None = None,
) -> None:
    """Record how a run ended, with its transcript: the partial results if it was cut short."""
    conn.execute(
        """
        UPDATE agent_runs
        SET status = %s, turns = %s, cost_usd = %s, final_text = %s, transcript = %s,
            error = %s, finished_at = now()
        WHERE id = %s
        """,
        (status, turns, cost_usd, final_text, Jsonb(transcript), error, run_id),
    )


def record_progress(conn: psycopg.Connection, run_id: UUID, *, turns: int, cost_usd: float) -> None:
    """Save a running run's turns and cost so far, for anyone polling it."""
    conn.execute(
        "UPDATE agent_runs SET turns = %s, cost_usd = %s WHERE id = %s",
        (turns, cost_usd, run_id),
    )


def record_tool_call(
    conn: psycopg.Connection,
    run_id: UUID,
    *,
    turn: int,
    tool_call_id: str,
    tool: str,
    arguments: dict[str, Any],
    result: str,
    outcome: ToolOutcome,
    latency_ms: float,
) -> None:
    conn.execute(
        """
        INSERT INTO tool_calls
            (run_id, turn, tool_call_id, tool, arguments, result, outcome, latency_ms)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (run_id, turn, tool_call_id, tool, Jsonb(arguments), result, outcome, latency_ms),
    )


def tool_calls_for(conn: psycopg.Connection, run_id: UUID) -> list[RecordedToolCall]:
    """A run's tool calls, in the order they were made."""
    rows = conn.execute(
        """
        SELECT turn, tool_call_id, tool, arguments, result, outcome, latency_ms, created_at
        FROM tool_calls WHERE run_id = %s ORDER BY id
        """,
        (run_id,),
    ).fetchall()
    return [RecordedToolCall(*row) for row in rows]


@dataclass(frozen=True)
class RunRecord:
    id: UUID
    task: Task
    status: RunStatus
    pool_id: UUID | None
    deck_id: UUID | None
    model: str
    turns: int
    cost_usd: float
    final_text: str | None
    error: str | None
    trace_id: str | None
    started_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True)
class ProposalSummary:
    id: UUID
    kind: Literal["deck", "changes"]
    status: str


def load_run(conn: psycopg.Connection, run_id: UUID) -> RunRecord | None:
    row = conn.execute(
        """
        SELECT id, task, status, pool_id, deck_id, model, turns, cost_usd::float8,
               final_text, error, trace_id, started_at, finished_at
        FROM agent_runs WHERE id = %s
        """,
        (run_id,),
    ).fetchone()
    return RunRecord(*row) if row else None


def run_proposals(conn: psycopg.Connection, run_id: UUID) -> list[ProposalSummary]:
    """The proposals a run made, in the order it made them."""
    rows = conn.execute(
        "SELECT id, kind, status FROM proposals WHERE run_id = %s ORDER BY created_at, id",
        (run_id,),
    ).fetchall()
    return [ProposalSummary(*row) for row in rows]


def interrupt_running_runs(conn: psycopg.Connection) -> int:
    """Mark runs still `running` as interrupted errors, and return how many.

    Called when a server starts: a run can only still be running then if the
    process that ran it stopped mid-run. Its transcript so far is lost, but its
    tool calls and proposals were committed turn by turn and stay.
    """
    rows = conn.execute(
        """
        UPDATE agent_runs
        SET status = 'error', error = 'interrupted: the server stopped mid-run',
            finished_at = now()
        WHERE status = 'running'
        RETURNING id
        """
    ).fetchall()
    return len(rows)


def fail_run(conn: psycopg.Connection, run_id: UUID, error: str) -> None:
    """Mark a run that failed outside the loop as an error, unless it already ended."""
    conn.execute(
        """
        UPDATE agent_runs SET status = 'error', error = %s, finished_at = now()
        WHERE id = %s AND status = 'running'
        """,
        (error, run_id),
    )
