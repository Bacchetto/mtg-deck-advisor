"""Approving, rejecting, applying and exporting: the user approves (GRD-2, GRD-5). ADR 0013.

The agent only ever creates proposals. Everything with an effect lives here,
and none of it is a tool:

- `approve_proposal` and `reject_proposal` record a user's decision, once per
  proposal. Only a pending (valid, undecided) proposal can be decided.
- `apply_proposal` saves a proposal as the deck's next version. It refuses
  without an approval, a second time, if the deck has moved on since the
  proposal was made, or if the deck is no longer legal (the pool may have
  changed since): it checks again rather than trusting the earlier check.
- `approve_export` and `export_deck`: a decklist leaves the system only for
  the exact version a user approved for export.

Every decision, action and refusal is written to the audit log. A refusal
changes nothing else and raises; its audit entry is written in its own
savepoint before raising, so it stays as long as the caller commits.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, NoReturn
from uuid import UUID

import psycopg

from mtg_deck_advisor.deck.facts import load_card_facts
from mtg_deck_advisor.deck.state import DeckState
from mtg_deck_advisor.deck.store import (
    decklist,
    load_deck,
    load_pool,
    name_for_commander,
    save_version,
)
from mtg_deck_advisor.guardrails.audit import record_audit
from mtg_deck_advisor.guardrails.commander import validate
from mtg_deck_advisor.observability.tracing import current_trace_id

Decision = Literal["approved", "rejected"]


class ApprovalError(Exception):
    """An action was refused; nothing was changed."""


class ApprovalRequiredError(ApprovalError):
    """The action needs a user's approval, and there is none."""


class ProposalStateError(ApprovalError):
    """The proposal can't take this step: decided already, applied, or no longer legal."""


class StaleProposalError(ApprovalError):
    """The deck has changed since the proposal was made against it."""


@dataclass(frozen=True)
class Approval:
    id: UUID
    kind: Literal["proposal", "export"]
    proposal_id: UUID | None
    deck_id: UUID | None
    deck_version: int | None
    decision: Decision
    actor: str
    note: str | None
    decided_at: datetime


def approve_proposal(
    conn: psycopg.Connection,
    proposal_id: UUID,
    *,
    note: str | None = None,
    via: dict[str, str] | None = None,
) -> Approval:
    """Record the user's approval. `via` says where they gave it (the channel and client)."""
    return _decide(conn, proposal_id, "approved", note, via)


def reject_proposal(
    conn: psycopg.Connection, proposal_id: UUID, *, reason: str, via: dict[str, str] | None = None
) -> Approval:
    return _decide(conn, proposal_id, "rejected", reason, via)


def apply_proposal(conn: psycopg.Connection, proposal_id: UUID) -> int:
    """Save an approved proposal as the deck's next version, and return the version."""
    subject = f"proposal:{proposal_id}"
    with conn.transaction():
        check = _check_apply(conn, proposal_id)
        if isinstance(check, _Applicable):
            version = save_version(conn, check.deck_id, check.state, proposal_id=proposal_id)
            conn.execute("UPDATE proposals SET status = 'applied' WHERE id = %s", (proposal_id,))
            record_audit(
                conn,
                "system",
                "apply",
                subject,
                {"deck": str(check.deck_id), "version": version, "approval": str(check.approval)},
            )
            _name_for_commander(conn, check.deck_id, check.state)
            return version
    # Outside the transaction above, so the refusal's audit entry isn't rolled back.
    _refuse(conn, subject, "apply_refused", check)


def _name_for_commander(conn: psycopg.Connection, deck_id: UUID, state: DeckState) -> None:
    """A deck with a default name takes its commander's when it first has one."""
    before = load_deck(conn, deck_id)
    renamed = name_for_commander(conn, deck_id, state.commander)
    if renamed is not None and before is not None:
        record_audit(
            conn,
            "system",
            "rename",
            f"deck:{deck_id}",
            {"from": before.name, "to": renamed, "reason": "commander"},
        )


@dataclass(frozen=True)
class _Applicable:
    deck_id: UUID
    state: DeckState
    approval: UUID


def _check_apply(conn: psycopg.Connection, proposal_id: UUID) -> _Applicable | ApprovalError:
    """What applying needs, or why the proposal can't be applied."""
    deck_id, status, base_version, payload = _lock_proposal(conn, proposal_id)
    if status == "applied":
        return ProposalStateError(f"proposal {proposal_id} has already been applied")
    approval = _proposal_approval(conn, proposal_id)
    if approval is None or approval.decision != "approved":
        return ApprovalRequiredError(
            f"proposal {proposal_id} has no approval from the user, so it can't be applied"
        )
    deck = load_deck(conn, deck_id)
    current = deck.version if deck else None
    if current != base_version:
        made_for = f"version {base_version}" if base_version else "an empty deck"
        return StaleProposalError(
            f"proposal {proposal_id} was made for {made_for}, but the deck is now at "
            f"version {current}; ask for a new proposal"
        )
    # Only a valid proposal can be approved, and a valid one always has a deck.
    state = DeckState.from_dict(payload["deck"])
    if problems := _problems(conn, deck_id, state):
        return ProposalStateError(
            f"proposal {proposal_id} is no longer legal: " + " ".join(problems)
        )
    return _Applicable(deck_id, state, approval.id)


def approve_export(
    conn: psycopg.Connection,
    deck_id: UUID,
    *,
    version: int | None = None,
    note: str | None = None,
    via: dict[str, str] | None = None,
) -> Approval:
    """Record a user's permission to export one version (the latest by default)."""
    deck = load_deck(conn, deck_id, version=version)
    if deck is None or deck.version is None:
        raise ProposalStateError(f"deck {deck_id} has no version {version or ''} to export")
    with conn.transaction():
        row = conn.execute(
            """
            INSERT INTO approvals (kind, deck_id, deck_version, decision, actor, note, trace_id)
            VALUES ('export', %s, %s, 'approved', 'user', %s, %s)
            RETURNING id, kind, proposal_id, deck_id, deck_version, decision, actor, note,
                decided_at
            """,
            (deck_id, deck.version, note, current_trace_id()),
        ).fetchone()
        details: dict[str, Any] = {"version": deck.version}
        if via:
            details["via"] = via
        record_audit(conn, "user", "approve_export", f"deck:{deck_id}", details)
    return _approval(row)


def export_deck(conn: psycopg.Connection, deck_id: UUID, *, version: int | None = None) -> str:
    """The deck as a plain decklist (`1 Card Name` lines, commander first), if approved."""
    subject = f"deck:{deck_id}"
    deck = load_deck(conn, deck_id, version=version)
    if deck is None or deck.state is None or deck.version is None:
        raise ProposalStateError(f"deck {deck_id} has no version {version or ''} to export")
    approved = conn.execute(
        "SELECT 1 FROM approvals WHERE kind = 'export' AND deck_id = %s AND deck_version = %s "
        "AND decision = 'approved'",
        (deck_id, deck.version),
    ).fetchone()
    if approved is None:
        _refuse(
            conn,
            subject,
            "export_refused",
            ApprovalRequiredError(
                f"version {deck.version} of deck {deck_id} has no export approval from the user"
            ),
        )
    text = decklist(conn, deck.state)
    with conn.transaction():
        record_audit(conn, "system", "export", subject, {"version": deck.version})
    return text


def _decide(
    conn: psycopg.Connection,
    proposal_id: UUID,
    decision: Decision,
    note: str | None,
    via: dict[str, str] | None,
) -> Approval:
    subject = f"proposal:{proposal_id}"
    with conn.transaction():
        _, status, _, _ = _lock_proposal(conn, proposal_id)
        if status != "pending":
            raise ProposalStateError(
                f"proposal {proposal_id} is {status}; only a pending proposal can be decided"
            )
        row = conn.execute(
            """
            INSERT INTO approvals (kind, proposal_id, decision, actor, note, trace_id)
            VALUES ('proposal', %s, %s, 'user', %s, %s)
            RETURNING id, kind, proposal_id, deck_id, deck_version, decision, actor, note,
                decided_at
            """,
            (proposal_id, decision, note, current_trace_id()),
        ).fetchone()
        conn.execute(
            "UPDATE proposals SET status = %s, decided_at = now() WHERE id = %s",
            (decision, proposal_id),
        )
        action = "approve" if decision == "approved" else "reject"
        details: dict[str, Any] = {"note": note} if note else {}
        if via:
            details["via"] = via
        record_audit(conn, "user", action, subject, details)
    return _approval(row)


def _lock_proposal(conn: psycopg.Connection, proposal_id: UUID) -> tuple[Any, ...]:
    """The proposal's deck, status, base version and payload, locked against races."""
    row = conn.execute(
        "SELECT deck_id, status, base_version, payload FROM proposals WHERE id = %s FOR UPDATE",
        (proposal_id,),
    ).fetchone()
    if row is None:
        raise ProposalStateError(f"there is no proposal {proposal_id}")
    return tuple(row)


def _proposal_approval(conn: psycopg.Connection, proposal_id: UUID) -> Approval | None:
    row = conn.execute(
        "SELECT id, kind, proposal_id, deck_id, deck_version, decision, actor, note, decided_at "
        "FROM approvals WHERE kind = 'proposal' "
        "AND proposal_id = %s",
        (proposal_id,),
    ).fetchone()
    return _approval(row) if row else None


def _problems(conn: psycopg.Connection, deck_id: UUID, state: DeckState) -> list[str]:
    """Every rules problem with `state`, checked against the deck's pool as it is now."""
    row = conn.execute("SELECT pool_id FROM decks WHERE id = %s", (deck_id,)).fetchone()
    pool = load_pool(conn, row[0]) if row else None
    if pool is None:
        return ["The deck's card pool no longer exists."]
    facts = load_card_facts(conn, [state.commander, *state.cards])
    return [v.message for v in validate(state, facts, pool.cards).violations]


def _refuse(conn: psycopg.Connection, subject: str, action: str, error: ApprovalError) -> NoReturn:
    """Audit a refusal, in its own savepoint, then raise it."""
    with conn.transaction():
        record_audit(conn, "system", action, subject, {"reason": str(error)})
    raise error


def _approval(row: tuple[Any, ...] | None) -> Approval:
    if row is None:  # INSERT ... RETURNING always returns a row
        raise RuntimeError("approval was not recorded")
    return Approval(*row)
