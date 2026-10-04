"""Proposals: the only thing the agent can create (GRD-1).

The agent never changes a deck. It proposes a whole deck or a set of changes;
code checks the result against the Commander rules and stores the proposal as
`pending` (for the user to approve or reject) or `invalid` (with every
problem, which goes back to the agent to fix).
"""

from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from mtg_deck_advisor.guardrails.audit import record_audit

ProposalKind = Literal["deck", "changes"]


def save_proposal(
    conn: psycopg.Connection,
    *,
    run_id: UUID | None,
    deck_id: UUID,
    kind: ProposalKind,
    payload: dict[str, Any],
    base_version: int | None,
    rationale: str,
    citations: list[str],
    problems: list[dict[str, Any]],
) -> UUID:
    """Store a checked proposal, `invalid` if it has problems, and audit it."""
    status = "invalid" if problems else "pending"
    row = conn.execute(
        """
        INSERT INTO proposals
            (run_id, deck_id, kind, payload, base_version, rationale, citations,
             validation, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (
            run_id,
            deck_id,
            kind,
            Jsonb(payload),
            base_version,
            rationale,
            Jsonb(citations),
            Jsonb({"problems": problems}),
            status,
        ),
    ).fetchone()
    if row is None:  # INSERT ... RETURNING always returns a row
        raise RuntimeError("proposal was not recorded")
    proposal_id: UUID = row[0]
    record_audit(
        conn,
        "agent",
        "propose",
        f"proposal:{proposal_id}",
        {"kind": kind, "status": status, "deck": str(deck_id), "problems": len(problems)},
    )
    return proposal_id
