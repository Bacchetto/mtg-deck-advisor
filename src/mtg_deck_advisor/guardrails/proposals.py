"""Proposals: the only thing the agent can create (GRD-1).

The agent never changes a deck. It proposes a whole deck or a set of changes;
code checks the result against the Commander rules and stores the proposal as
`pending` (for the user to approve or reject) or `invalid` (with every
problem, which goes back to the agent to fix).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from mtg_deck_advisor.deck.store import card_names
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


@dataclass(frozen=True)
class ProposalRecord:
    id: UUID
    run_id: UUID | None
    deck_id: UUID
    kind: ProposalKind
    status: str
    payload: dict[str, Any]
    base_version: int | None
    rationale: str
    citations: list[str]
    # The checks' verdict: every problem found; empty for a valid proposal.
    problems: list[dict[str, Any]]
    created_at: datetime
    decided_at: datetime | None


PROPOSAL_COLUMNS = """
    id, run_id, deck_id, kind, status, payload, base_version, rationale, citations,
    coalesce(validation->'problems', '[]'::jsonb), created_at, decided_at
"""


def load_proposal(conn: psycopg.Connection, proposal_id: UUID) -> ProposalRecord | None:
    row = conn.execute(
        f"SELECT {PROPOSAL_COLUMNS} FROM proposals WHERE id = %s",  # noqa: S608 (a constant)
        (proposal_id,),
    ).fetchone()
    return ProposalRecord(*row) if row else None


def deck_proposals(conn: psycopg.Connection, deck_id: UUID) -> list[ProposalRecord]:
    """Every proposal for a deck, oldest first."""
    rows = conn.execute(
        f"SELECT {PROPOSAL_COLUMNS} FROM proposals "  # noqa: S608 (a constant)
        "WHERE deck_id = %s ORDER BY created_at, id",
        (deck_id,),
    ).fetchall()
    return [ProposalRecord(*row) for row in rows]


def named_changes(
    conn: psycopg.Connection, proposal: ProposalRecord
) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    """A change set's adds and removes as (card name, count); empty for a whole deck."""
    if proposal.kind != "changes":
        return [], []
    add, remove = proposal.payload["add"], proposal.payload["remove"]
    names = card_names(conn, [UUID(card) for card in (*add, *remove)])
    return (
        [(names[UUID(card)], count) for card, count in add.items()],
        [(names[UUID(card)], count) for card, count in remove.items()],
    )
