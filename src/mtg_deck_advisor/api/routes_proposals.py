"""The user's steps over HTTP: read proposals, decide, apply, export (ENG-5, GRD-2, GRD-5).

These are the only routes that change a deck or let one leave the system, and
each is a thin call to `guardrails.approvals`, which does the checking: an
apply or export without the user's approval is refused (403), and a proposal
that can't take the step is a conflict (409). A refusal's audit entry is
committed before the error goes back, so the record of the attempt stays.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from mtg_deck_advisor.api.services import Connection
from mtg_deck_advisor.deck.state import DeckState
from mtg_deck_advisor.deck.store import DeckName, deck_versions, decklist, load_deck, rename_deck
from mtg_deck_advisor.guardrails.approvals import (
    Approval,
    ApprovalError,
    ApprovalRequiredError,
    apply_proposal,
    approve_export,
    approve_proposal,
    export_deck,
    reject_proposal,
)
from mtg_deck_advisor.guardrails.audit import record_audit
from mtg_deck_advisor.guardrails.proposals import load_proposal, named_changes

router = APIRouter()

REFUSALS: dict[int | str, dict[str, Any]] = {
    403: {"description": "The user hasn't approved this."},
    404: {"description": "No such proposal, deck or version."},
    409: {"description": "The proposal or deck can't take this step now."},
}


# --- models -------------------------------------------------------------------------------


class Problem(BaseModel):
    code: str
    message: str
    rule: str | None = Field(default=None, description="The rule broken, where there is one.")


class CardCount(BaseModel):
    name: str
    count: int


class Changes(BaseModel):
    add: list[CardCount]
    remove: list[CardCount]


class ProposalView(BaseModel):
    id: UUID
    run_id: UUID | None
    deck_id: UUID
    kind: Literal["deck", "changes"]
    status: Literal["pending", "invalid", "approved", "rejected", "applied"]
    rationale: str
    citations: list[str]
    problems: list[Problem] = Field(description="Every problem the checks found; empty if valid.")
    base_version: int | None = Field(description="The deck version the proposal was made for.")
    changes: Changes | None = Field(description="For a change set: what it adds and removes.")
    decklist: str | None = Field(description="The deck as the proposal would leave it.")
    created_at: datetime
    decided_at: datetime | None


class Decision(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class Rejection(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class ApprovalView(BaseModel):
    id: UUID
    kind: Literal["proposal", "export"]
    proposal_id: UUID | None
    deck_id: UUID | None
    deck_version: int | None
    decision: Literal["approved", "rejected"]
    actor: str
    note: str | None
    decided_at: datetime


class Applied(BaseModel):
    deck_id: UUID
    version: int


class VersionView(BaseModel):
    version: int
    proposal_id: UUID | None
    created_at: datetime


class DeckView(BaseModel):
    id: UUID
    pool_id: UUID
    name: str
    version: int | None = Field(description="The latest version; none until one is applied.")
    versions: list[VersionView]
    decklist: str | None


class DeckRename(BaseModel):
    name: DeckName


class DeckNamed(BaseModel):
    id: UUID
    name: str
    previous_name: str


# --- proposals ------------------------------------------------------------------------------


@router.get("/proposals/{proposal_id}", tags=["proposals"], responses={404: REFUSALS[404]})
def get_proposal(proposal_id: UUID, conn: Connection) -> ProposalView:
    """A proposal: its rationale, citations, every problem found, and the deck it would make."""
    proposal = load_proposal(conn, proposal_id)
    if proposal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"there is no proposal {proposal_id}")
    changes = None
    if proposal.kind == "changes":
        add, remove = named_changes(conn, proposal)
        changes = Changes(
            add=[CardCount(name=name, count=count) for name, count in add],
            remove=[CardCount(name=name, count=count) for name, count in remove],
        )
    deck = proposal.payload.get("deck")
    return ProposalView(
        id=proposal.id,
        run_id=proposal.run_id,
        deck_id=proposal.deck_id,
        kind=proposal.kind,
        status=proposal.status,
        rationale=proposal.rationale,
        citations=proposal.citations,
        problems=[Problem(**problem) for problem in proposal.problems],
        base_version=proposal.base_version,
        changes=changes,
        decklist=decklist(conn, DeckState.from_dict(deck)) if deck else None,
        created_at=proposal.created_at,
        decided_at=proposal.decided_at,
    )


@router.post("/proposals/{proposal_id}/approval", tags=["proposals"], responses=REFUSALS)
def approve(proposal_id: UUID, body: Decision, conn: Connection) -> ApprovalView:
    """Approve a pending proposal. Only a valid, undecided proposal can be approved."""
    _require_proposal(conn, proposal_id)
    with _refusals(conn):
        approval = approve_proposal(conn, proposal_id, note=body.note)
    return _approval(conn, approval)


@router.post("/proposals/{proposal_id}/rejection", tags=["proposals"], responses=REFUSALS)
def reject(proposal_id: UUID, body: Rejection, conn: Connection) -> ApprovalView:
    """Reject a pending proposal, with the reason."""
    _require_proposal(conn, proposal_id)
    with _refusals(conn):
        rejection = reject_proposal(conn, proposal_id, reason=body.reason)
    return _approval(conn, rejection)


@router.post("/proposals/{proposal_id}/application", tags=["proposals"], responses=REFUSALS)
def apply(proposal_id: UUID, conn: Connection) -> Applied:
    """Save an approved proposal as the deck's next version.

    Refused without the user's approval (403), and when the proposal was
    already applied, was made for an older version, or is no longer legal (409).
    """
    proposal = _require_proposal(conn, proposal_id)
    with _refusals(conn):
        version = apply_proposal(conn, proposal_id)
    conn.commit()
    return Applied(deck_id=proposal, version=version)


# --- decks and exports ------------------------------------------------------------------------


@router.get("/decks/{deck_id}", tags=["decks"], responses={404: REFUSALS[404]})
def get_deck(deck_id: UUID, conn: Connection) -> DeckView:
    """A deck: its saved versions and its latest decklist."""
    deck = load_deck(conn, deck_id)
    if deck is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"there is no deck {deck_id}")
    return DeckView(
        id=deck.id,
        pool_id=deck.pool_id,
        name=deck.name,
        version=deck.version,
        versions=[VersionView(**vars(v)) for v in deck_versions(conn, deck_id)],
        decklist=decklist(conn, deck.state) if deck.state else None,
    )


@router.patch("/decks/{deck_id}", tags=["decks"], responses={404: REFUSALS[404]})
def rename(deck_id: UUID, body: DeckRename, conn: Connection) -> DeckNamed:
    """Rename a deck. Only its name changes; its versions and proposals stay as they are."""
    with conn.transaction():
        previous = rename_deck(conn, deck_id, body.name)
        if previous is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"there is no deck {deck_id}")
        details = {"from": previous, "to": body.name}
        record_audit(conn, "user", "rename", f"deck:{deck_id}", details)
    return DeckNamed(id=deck_id, name=body.name, previous_name=previous)


@router.post(
    "/decks/{deck_id}/versions/{version}/export-approval", tags=["decks"], responses=REFUSALS
)
def approve_deck_export(
    deck_id: UUID, version: int, body: Decision, conn: Connection
) -> ApprovalView:
    """Approve exporting one version of a deck. The approval covers only that version."""
    _require_version(conn, deck_id, version)
    with _refusals(conn):
        approval = approve_export(conn, deck_id, version=version, note=body.note)
    return _approval(conn, approval)


@router.get(
    "/decks/{deck_id}/versions/{version}/export",
    tags=["decks"],
    response_class=PlainTextResponse,
    responses={200: {"content": {"text/plain": {}}}, **REFUSALS},
)
def export(deck_id: UUID, version: int, conn: Connection) -> str:
    """The version as a plain decklist (`1 Card Name` lines, commander first), if approved."""
    _require_version(conn, deck_id, version)
    with _refusals(conn):
        text = export_deck(conn, deck_id, version=version)
    conn.commit()
    return text


# --- helpers ----------------------------------------------------------------------------------


@contextmanager
def _refusals(conn: Connection) -> Iterator[None]:
    """Turn a refused step into 403 or 409, keeping its audit entry."""
    try:
        yield
    except ApprovalError as exc:
        conn.commit()
        code = 403 if isinstance(exc, ApprovalRequiredError) else 409
        raise HTTPException(code, str(exc)) from exc


def _require_proposal(conn: Connection, proposal_id: UUID) -> UUID:
    """The proposal's deck; 404 if there is no such proposal."""
    proposal = load_proposal(conn, proposal_id)
    if proposal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"there is no proposal {proposal_id}")
    return proposal.deck_id


def _require_version(conn: Connection, deck_id: UUID, version: int) -> None:
    deck = load_deck(conn, deck_id, version=version)
    if deck is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"deck {deck_id} has no version {version}")


def _approval(conn: Connection, approval: Approval) -> ApprovalView:
    conn.commit()
    return ApprovalView(**vars(approval))
