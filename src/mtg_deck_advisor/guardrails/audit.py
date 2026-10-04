"""The audit log: who did what to what, under which trace (GRD-5).

Every approval, rejection and executed action is written here. The table is
append-only: triggers refuse updates, deletes and truncation, so the record
can't be quietly rewritten after the fact.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import psycopg
from psycopg.types.json import Jsonb

from mtg_deck_advisor.observability.tracing import current_trace_id

Actor = Literal["user", "agent", "system"]


@dataclass(frozen=True)
class AuditEntry:
    at: datetime
    actor: Actor
    action: str
    subject: str
    details: dict[str, Any]
    trace_id: str | None


def record_audit(
    conn: psycopg.Connection,
    actor: Actor,
    action: str,
    subject: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Append one entry, tagged with the current trace ID.

    `subject` names what was acted on as 'kind:id', such as 'proposal:42'.
    """
    conn.execute(
        """
        INSERT INTO audit_log (actor, action, subject, details, trace_id)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (actor, action, subject, Jsonb(details or {}), current_trace_id()),
    )


def audit_entries(conn: psycopg.Connection, subject: str) -> list[AuditEntry]:
    """Every entry for `subject`, oldest first."""
    rows = conn.execute(
        """
        SELECT at, actor, action, subject, details, trace_id FROM audit_log
        WHERE subject = %s ORDER BY id
        """,
        (subject,),
    ).fetchall()
    return [AuditEntry(*row) for row in rows]
