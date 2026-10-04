"""The model proposes, code decides, the user approves (GRD-1, GRD-2, GRD-5)."""

from collections.abc import Iterator
from uuid import UUID

import psycopg
import pytest

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.deck.state import DeckState
from mtg_deck_advisor.deck.store import load_deck
from mtg_deck_advisor.guardrails.approvals import (
    ApprovalRequiredError,
    ProposalStateError,
    StaleProposalError,
    apply_proposal,
    approve_export,
    approve_proposal,
    export_deck,
    reject_proposal,
)
from mtg_deck_advisor.guardrails.audit import audit_entries
from tests.integration.test_agent_tools import (
    ATRAXA,
    BASICS,
    COHORT,
    LEGAL_CARDS,
    RATS,
    Session,
    card_id,
    loaded,  # noqa: F401 (a fixture)
    session_for,
)


@pytest.fixture
def conn(loaded: Settings) -> Iterator[psycopg.Connection]:  # noqa: F811
    with connect(loaded) as connection:
        yield connection


def drafted(conn: psycopg.Connection, cards: list[dict[str, object]] | None = None) -> Session:
    """A draft run that has proposed a deck."""
    session = session_for(conn, "draft")
    session.call(
        "propose_deck", commander=ATRAXA, cards=cards or LEGAL_CARDS, rationale="Rats and ramp."
    )
    return session


def only_proposal(session: Session) -> UUID:
    (proposal,) = session.context.proposals
    return proposal


def status(conn: psycopg.Connection, proposal: UUID) -> str:
    row = conn.execute("SELECT status FROM proposals WHERE id = %s", (proposal,)).fetchone()
    assert row is not None
    return str(row[0])


def actions(conn: psycopg.Connection, subject: str) -> list[tuple[str, str]]:
    return [(entry.actor, entry.action) for entry in audit_entries(conn, subject)]


def deck_versions(conn: psycopg.Connection) -> int:
    row = conn.execute("SELECT count(*) FROM deck_versions").fetchone()
    assert row is not None
    return int(row[0])


# --- approving and applying ------------------------------------------------------------


def test_an_approved_proposal_is_applied_as_a_new_version(conn: psycopg.Connection) -> None:
    session = drafted(conn)
    proposal = only_proposal(session)
    deck_id = session.context.deck_id
    assert deck_id is not None

    approval = approve_proposal(conn, proposal, note="Looks good.")
    version = apply_proposal(conn, proposal)

    assert (approval.decision, approval.actor, approval.proposal_id) == (
        "approved",
        "user",
        proposal,
    )
    assert version == 1
    deck = load_deck(conn, deck_id)
    assert deck is not None and deck.state == session.context.draft
    assert conn.execute(
        "SELECT proposal_id FROM deck_versions WHERE deck_id = %s", (deck_id,)
    ).fetchone() == (proposal,)
    assert status(conn, proposal) == "applied"
    assert actions(conn, f"proposal:{proposal}") == [
        ("agent", "propose"),
        ("user", "approve"),
        ("system", "apply"),
    ]


def test_apply_without_an_approval_is_refused_and_changes_nothing(
    conn: psycopg.Connection,
) -> None:
    proposal = only_proposal(drafted(conn))

    with pytest.raises(ApprovalRequiredError, match="approv"):
        apply_proposal(conn, proposal)

    assert deck_versions(conn) == 0
    assert status(conn, proposal) == "pending"
    assert actions(conn, f"proposal:{proposal}")[-1] == ("system", "apply_refused")


def test_a_rejected_proposal_cannot_be_applied_or_approved(conn: psycopg.Connection) -> None:
    proposal = only_proposal(drafted(conn))

    rejection = reject_proposal(conn, proposal, reason="Too many rats.")

    assert (rejection.decision, rejection.note) == ("rejected", "Too many rats.")
    assert status(conn, proposal) == "rejected"
    with pytest.raises(ApprovalRequiredError):
        apply_proposal(conn, proposal)
    with pytest.raises(ProposalStateError, match="rejected"):
        approve_proposal(conn, proposal)
    assert deck_versions(conn) == 0
    assert actions(conn, f"proposal:{proposal}") == [
        ("agent", "propose"),
        ("user", "reject"),
        ("system", "apply_refused"),
    ]


def test_an_invalid_proposal_cannot_be_approved(conn: psycopg.Connection) -> None:
    proposal = only_proposal(drafted(conn, cards=[{"name": "Sol Ring"}]))
    assert status(conn, proposal) == "invalid"

    with pytest.raises(ProposalStateError, match="invalid"):
        approve_proposal(conn, proposal)

    assert status(conn, proposal) == "invalid"


def test_a_proposal_is_applied_only_once(conn: psycopg.Connection) -> None:
    proposal = only_proposal(drafted(conn))
    approve_proposal(conn, proposal)
    apply_proposal(conn, proposal)

    with pytest.raises(ProposalStateError, match="applied"):
        apply_proposal(conn, proposal)

    assert deck_versions(conn) == 1


def test_changes_made_against_an_older_version_are_refused(conn: psycopg.Connection) -> None:
    rats, sol_ring, cohort = card_id(RATS), card_id("Sol Ring"), card_id(COHORT)
    basics = {card_id(name): count for name, count in BASICS.items()}
    base = DeckState.new(card_id(ATRAXA), {rats: 60, sol_ring: 1, cohort: 1, **basics})
    session = session_for(conn, "refine", base=base)
    session.call(
        "propose_changes",
        add=[{"name": "Delver of Secrets"}],
        remove=[{"name": "Island"}],
        rationale="r",
    )
    session.call(
        "propose_changes",
        add=[{"name": "Delver of Secrets"}],
        remove=[{"name": "Forest"}],
        rationale="r",
    )
    first, second = session.context.proposals
    approve_proposal(conn, first)
    approve_proposal(conn, second)
    apply_proposal(conn, first)  # the deck is now at version 2

    with pytest.raises(StaleProposalError, match="version 1"):
        apply_proposal(conn, second)

    assert deck_versions(conn) == 2
    assert status(conn, second) == "approved"


def test_a_proposal_that_is_no_longer_legal_is_refused_at_apply(
    conn: psycopg.Connection,
) -> None:
    session = drafted(conn)
    proposal = only_proposal(session)
    approve_proposal(conn, proposal)
    # The user's pool changes after approval: Sol Ring is gone.
    pool = session.context.pool
    assert pool is not None
    conn.execute(
        "DELETE FROM pool_cards WHERE pool_id = %s AND oracle_id = %s",
        (pool.id, card_id("Sol Ring")),
    )

    with pytest.raises(ProposalStateError, match="Sol Ring"):
        apply_proposal(conn, proposal)

    assert deck_versions(conn) == 0


# --- exporting --------------------------------------------------------------------------


def applied_deck(conn: psycopg.Connection) -> UUID:
    session = drafted(conn)
    proposal = only_proposal(session)
    approve_proposal(conn, proposal)
    apply_proposal(conn, proposal)
    assert session.context.deck_id is not None
    return session.context.deck_id


def test_export_without_an_approval_is_refused(conn: psycopg.Connection) -> None:
    deck_id = applied_deck(conn)

    with pytest.raises(ApprovalRequiredError, match="approv"):
        export_deck(conn, deck_id)

    assert actions(conn, f"deck:{deck_id}") == [("system", "export_refused")]


def test_an_approved_export_is_a_plain_decklist(conn: psycopg.Connection) -> None:
    deck_id = applied_deck(conn)

    approval = approve_export(conn, deck_id)
    decklist = export_deck(conn, deck_id)

    assert (approval.decision, approval.deck_version) == ("approved", 1)
    lines = decklist.splitlines()
    assert lines[0] == f"1 {ATRAXA}"  # the commander first
    assert sorted(lines[1:], key=lambda line: line.split(" ", 1)[1]) == lines[1:]
    assert "60 Relentless Rats" in lines and "10 Plains" in lines and "1 Sol Ring" in lines
    assert sum(int(line.split(" ", 1)[0]) for line in lines) == 100
    assert actions(conn, f"deck:{deck_id}") == [("user", "approve_export"), ("system", "export")]


def test_an_export_approval_covers_only_the_version_approved(conn: psycopg.Connection) -> None:
    deck_id = applied_deck(conn)
    approve_export(conn, deck_id)
    # A new version is applied after the export was approved.
    session = session_for(conn, "refine")
    session.context.deck_id = deck_id
    session.call(
        "propose_changes",
        add=[{"name": "Delver of Secrets"}],
        remove=[{"name": "Island"}],
        rationale="r",
    )
    (change,) = session.context.proposals
    approve_proposal(conn, change)
    apply_proposal(conn, change)

    with pytest.raises(ApprovalRequiredError, match="version 2"):
        export_deck(conn, deck_id)
    assert export_deck(conn, deck_id, version=1).startswith(f"1 {ATRAXA}")


# --- the approval records themselves -----------------------------------------------------


def test_only_a_user_can_approve_and_decisions_cannot_be_changed(
    conn: psycopg.Connection,
) -> None:
    proposal = only_proposal(drafted(conn))
    approve_proposal(conn, proposal)

    with connect_like(conn) as other, pytest.raises(psycopg.errors.RaiseException):
        other.execute("UPDATE approvals SET decision = 'rejected'")
    with connect_like(conn) as other, pytest.raises(psycopg.errors.RaiseException):
        other.execute("DELETE FROM approvals")
    with connect_like(conn) as other, pytest.raises(psycopg.errors.CheckViolation):
        other.execute(
            "INSERT INTO approvals (kind, proposal_id, decision, actor) "
            "VALUES ('proposal', %s, 'approved', 'agent')",
            (proposal,),
        )


def connect_like(conn: psycopg.Connection) -> psycopg.Connection:
    """A second connection to the same database, so a failure there leaves `conn` usable."""
    conn.commit()
    return psycopg.connect(conn.info.dsn, password=conn.info.password)
