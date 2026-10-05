"""The user's steps through MCP: decisions confirmed by the user in a form (#107; GRD-2, GRD-5).

The model can call approve_proposal, but calling it isn't consent: the server
asks the user directly with an MCP elicitation form, and only the user's
Accept records an approval. Apply and export still need that record.
"""

# Fixtures imported from other test modules are named again as test parameters,
# which is how pytest injects them; ruff reads that as a redefinition.
# ruff: noqa: F811

from typing import Any, Literal
from uuid import UUID

import anyio
import psycopg
import pytest
from mcp import Client, types

from mtg_deck_advisor.guardrails.audit import audit_entries
from mtg_deck_advisor.mcp_server.server import McpSession, build_server
from tests.integration.test_agent_tools import ATRAXA, LEGAL_CARDS, loaded  # noqa: F401
from tests.integration.test_mcp_server import conn, pool_id, session, text  # noqa: F401

Step = tuple[str, dict[str, Any]]
Answer = Literal["accept", "decline", "cancel"]


class User:
    """Answers the server's forms as a person would, and remembers what it was shown."""

    def __init__(self, *answers: Answer, note: str = "") -> None:
        self.answers = list(answers)
        self.note = note
        self.shown: list[str] = []

    async def __call__(self, context: Any, params: types.ElicitRequestParams) -> types.ElicitResult:
        self.shown.append(params.message)
        action = self.answers.pop(0)
        content: dict[str, Any] | None = (
            {"note": self.note} if action == "accept" and self.note else None
        )
        return types.ElicitResult(action=action, content=content)


# The two ways a server asks: a request sent mid-call (the handshake-era
# protocol, "legacy"), or an "input required" result the client answers by
# calling again (the 2026-07-28 revision, the SDK's default). Claude Code
# speaks both, so both are tested.
MODES = pytest.mark.parametrize("mode", ["legacy", "auto"])


def calls(
    state: McpSession, user: User | None, *steps: Step, mode: str = "auto"
) -> list[types.CallToolResult]:
    async def go() -> list[types.CallToolResult]:
        async with Client(build_server(state), elicitation_callback=user, mode=mode) as client:
            return [await client.call_tool(name, arguments) for name, arguments in steps]

    return anyio.run(go)


def drafted(conn: psycopg.Connection, pool_id: UUID, state: McpSession) -> tuple[str, str]:
    """A new deck with a legal, pending proposal: its deck and proposal IDs."""
    (created,) = calls(state, None, ("new_deck", {"pool_id": str(pool_id), "name": "MCP"}))
    deck_id = text(created).split()[-1].rstrip(".")
    proposal = {"deck_id": deck_id, "commander": ATRAXA, "cards": LEGAL_CARDS, "rationale": "Rats."}
    (proposed,) = calls(state, None, ("propose_deck", proposal))
    assert not proposed.is_error, text(proposed)
    row = conn.execute("SELECT id FROM proposals WHERE deck_id = %s", (deck_id,)).fetchone()
    assert row is not None
    return deck_id, str(row[0])


def approvals(conn: psycopg.Connection) -> list[tuple[Any, ...]]:
    rows = conn.execute("SELECT kind, decision, actor, note FROM approvals ORDER BY decided_at")
    return [tuple(row) for row in rows]


def actions(conn: psycopg.Connection, subject: str) -> list[tuple[str, str]]:
    return [(entry.actor, entry.action) for entry in audit_entries(conn, subject)]


# --- the whole build in one session ----------------------------------------------------


@MODES
def test_a_user_builds_a_deck_from_draft_to_export_without_leaving_the_client(
    conn: psycopg.Connection, pool_id: UUID, mode: str
) -> None:
    state = session(conn)
    deck_id, proposal_id = drafted(conn, pool_id, state)
    user = User("accept", "accept", note="Looks good.")

    listed, shown, approved, applied, export_ok, exported = calls(
        state,
        user,
        ("list_proposals", {"deck_id": deck_id}),
        ("show_proposal", {"proposal_id": proposal_id}),
        ("approve_proposal", {"proposal_id": proposal_id}),
        ("apply_proposal", {"proposal_id": proposal_id}),
        ("approve_export", {"deck_id": deck_id}),
        ("export_deck", {"deck_id": deck_id}),
        mode=mode,
    )

    assert proposal_id in text(listed) and "pending" in text(listed)
    assert "60 Relentless Rats" in text(shown) and "Rats." in text(shown)
    assert not approved.is_error and "approved" in text(approved).lower()
    assert not applied.is_error and "version 1" in text(applied)
    assert not export_ok.is_error, text(export_ok)
    lines = text(exported).split("<untrusted>\n", 1)[1].split("\n</untrusted>", 1)[0].splitlines()
    assert lines[0] == f"1 {ATRAXA}"
    assert sum(int(line.split(" ", 1)[0]) for line in lines) == 100
    # The forms are the server's own account of what is being approved.
    proposal_form, export_form = user.shown
    assert ATRAXA in proposal_form and "100 cards" in proposal_form
    assert "version 1" in export_form
    # Recorded as the user's decisions, with the note they typed.
    assert approvals(conn) == [
        ("proposal", "approved", "user", "Looks good."),
        ("export", "approved", "user", "Looks good."),
    ]
    assert actions(conn, f"proposal:{proposal_id}") == [
        ("agent", "propose"),
        ("user", "approve"),
        ("system", "apply"),
    ]
    assert ("user", "approve_export") in actions(conn, f"deck:{deck_id}")
    assert ("system", "export") in actions(conn, f"deck:{deck_id}")


def test_a_rejection_is_confirmed_by_the_user_too(conn: psycopg.Connection, pool_id: UUID) -> None:
    state = session(conn)
    _, proposal_id = drafted(conn, pool_id, state)
    user = User("accept")

    (rejected,) = calls(
        state,
        user,
        ("reject_proposal", {"proposal_id": proposal_id, "reason": "Too many rats."}),
    )

    assert not rejected.is_error, text(rejected)
    assert "Too many rats." in user.shown[0]
    assert approvals(conn) == [("proposal", "rejected", "user", "Too many rats.")]


# --- what the model can't do ------------------------------------------------------------


@MODES
def test_when_the_user_declines_nothing_is_approved(
    conn: psycopg.Connection, pool_id: UUID, mode: str
) -> None:
    state = session(conn)
    _, proposal_id = drafted(conn, pool_id, state)

    declined, applied = calls(
        state,
        User("decline"),
        ("approve_proposal", {"proposal_id": proposal_id}),
        ("apply_proposal", {"proposal_id": proposal_id}),
        mode=mode,
    )

    assert "declined" in text(declined) and "nothing changed" in text(declined).lower()
    assert applied.is_error and "no approval" in text(applied)
    assert approvals(conn) == []
    status = conn.execute("SELECT status FROM proposals WHERE id = %s", (proposal_id,)).fetchone()
    assert status == ("pending",)
    assert ("user", "approval_declined") in actions(conn, f"proposal:{proposal_id}")


@MODES
def test_a_client_that_cant_ask_the_user_cant_approve(
    conn: psycopg.Connection, pool_id: UUID, mode: str
) -> None:
    state = session(conn)
    _, proposal_id = drafted(conn, pool_id, state)

    approved, rejected = calls(
        state,
        None,  # no elicitation support
        ("approve_proposal", {"proposal_id": proposal_id}),
        ("reject_proposal", {"proposal_id": proposal_id, "reason": "r"}),
        mode=mode,
    )

    for result in (approved, rejected):
        assert result.is_error and "mtg-advisor" in text(result)
    assert approvals(conn) == []


def test_consent_cant_be_passed_as_an_argument(conn: psycopg.Connection, pool_id: UUID) -> None:
    state = session(conn)
    _, proposal_id = drafted(conn, pool_id, state)
    user = User()  # would fail if it were asked

    (result,) = calls(
        state,
        user,
        ("approve_proposal", {"proposal_id": proposal_id, "confirmed": True, "actor": "user"}),
    )

    assert result.is_error and "Invalid arguments" in text(result)
    assert user.shown == []
    assert approvals(conn) == []


def test_steps_that_cant_happen_are_refused_before_asking(
    conn: psycopg.Connection, pool_id: UUID
) -> None:
    state = session(conn)
    deck_id, _ = drafted(conn, pool_id, state)
    user = User()

    unknown, malformed, unsaved_export, no_export = calls(
        state,
        user,
        ("approve_proposal", {"proposal_id": "00000000-0000-0000-0000-000000000000"}),
        ("show_proposal", {"proposal_id": "nope"}),
        ("approve_export", {"deck_id": deck_id}),  # nothing saved yet
        ("export_deck", {"deck_id": deck_id}),
    )

    assert unknown.is_error and "no proposal" in text(unknown)
    assert malformed.is_error
    assert unsaved_export.is_error and "nothing saved" in text(unsaved_export)
    assert no_export.is_error
    assert user.shown == []
