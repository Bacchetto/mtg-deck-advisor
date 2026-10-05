"""The user's steps through MCP, with decisions the user allows in the client (#107; GRD-2).

A decision tool (approve, reject, approve an export) is marked as requiring
user interaction, so Claude Code always shows the user an Allow/Deny prompt
for it, which no setting, mode or hook can answer for them. The prompt shows
the call's arguments, and the `confirming` argument must match the server's
own summary of the decision, so the text the user allows can't be slanted by
the model. Only clients known to honour the marking can make decisions.
"""

# Fixtures imported from other test modules are named again as test parameters,
# which is how pytest injects them; ruff reads that as a redefinition.
# ruff: noqa: F811

import re
from typing import Any
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
DECISION_TOOLS = ("archive_deck", "approve_proposal", "reject_proposal", "approve_export")
# The handshake-era protocol ("legacy") and the 2026-07-28 revision (the SDK's
# default) identify the client differently; Claude Code speaks both.
MODES = pytest.mark.parametrize("mode", ["legacy", "auto"])


def calls(
    state: McpSession, *steps: Step, mode: str = "auto", client: str = "claude-code"
) -> list[types.CallToolResult]:
    info = types.Implementation(name=client, version="1.0")

    async def go() -> list[types.CallToolResult]:
        async with Client(build_server(state), mode=mode, client_info=info) as session:
            return [await session.call_tool(name, arguments) for name, arguments in steps]

    return anyio.run(go)


def confirming(result: types.CallToolResult) -> str:
    """The summary a result tells the client to confirm, as the server words it."""
    match = re.search(r'confirming: "(.+)"$', text(result), re.MULTILINE)
    assert match, text(result)
    return match.group(1)


def drafted(conn: psycopg.Connection, pool_id: UUID, state: McpSession) -> tuple[str, str]:
    """A new deck with a legal, pending proposal: its deck and proposal IDs."""
    (created,) = calls(state, ("new_deck", {"pool_id": str(pool_id), "name": "MCP"}))
    deck_id = text(created).split()[-1].rstrip(".")
    proposal = {"deck_id": deck_id, "commander": ATRAXA, "cards": LEGAL_CARDS, "rationale": "Rats."}
    (proposed,) = calls(state, ("propose_deck", proposal))
    assert not proposed.is_error, text(proposed)
    row = conn.execute("SELECT id FROM proposals WHERE deck_id = %s", (deck_id,)).fetchone()
    assert row is not None
    return deck_id, str(row[0])


def approvals(conn: psycopg.Connection) -> list[tuple[Any, ...]]:
    rows = conn.execute("SELECT kind, decision, actor, note FROM approvals ORDER BY decided_at")
    return [tuple(row) for row in rows]


def actions(conn: psycopg.Connection, subject: str) -> list[tuple[str, str]]:
    return [(entry.actor, entry.action) for entry in audit_entries(conn, subject)]


# --- the tools ---------------------------------------------------------------------------


def test_only_the_decision_tools_require_the_users_interaction(conn: psycopg.Connection) -> None:
    async def go() -> list[types.Tool]:
        async with Client(build_server(session(conn))) as client:
            return (await client.list_tools()).tools

    tools = {tool.name: tool for tool in anyio.run(go)}

    marked = {name for name, tool in tools.items() if tool.meta}
    assert marked == set(DECISION_TOOLS)
    for name in DECISION_TOOLS:
        assert tools[name].meta == {"anthropic/requiresUserInteraction": True}
        assert "confirming" in tools[name].input_schema["required"]


# --- the whole build in one session -------------------------------------------------------


@MODES
def test_a_user_builds_a_deck_from_draft_to_export_without_leaving_the_client(
    conn: psycopg.Connection, pool_id: UUID, mode: str
) -> None:
    state = session(conn)
    deck_id, proposal_id = drafted(conn, pool_id, state)

    listed, shown = calls(
        state,
        ("list_proposals", {"deck_id": deck_id}),
        ("show_proposal", {"proposal_id": proposal_id}),
        mode=mode,
    )
    summary = confirming(shown)
    approved, applied = calls(
        state,
        ("approve_proposal", {"proposal_id": proposal_id, "confirming": summary}),
        ("apply_proposal", {"proposal_id": proposal_id}),
        mode=mode,
    )
    export_summary = confirming(applied)
    export_ok, exported = calls(
        state,
        ("approve_export", {"deck_id": deck_id, "confirming": export_summary}),
        ("export_deck", {"deck_id": deck_id}),
        mode=mode,
    )

    assert proposal_id in text(listed) and "pending" in text(listed)
    assert "60 Relentless Rats" in text(shown) and "Rats." in text(shown)
    # The summaries are the server's own account of each decision.
    assert summary == f"Approve: {ATRAXA} as commander, 100 cards, for deck 'MCP'"
    assert export_summary == f"Export: version 1 of deck 'MCP', {ATRAXA} as commander, 100 cards"
    assert not approved.is_error, text(approved)
    assert not applied.is_error and "version 1" in text(applied)
    assert not export_ok.is_error, text(export_ok)
    lines = text(exported).split("<untrusted>\n", 1)[1].split("\n</untrusted>", 1)[0].splitlines()
    assert lines[0] == f"1 {ATRAXA}"
    assert sum(int(line.split(" ", 1)[0]) for line in lines) == 100
    assert approvals(conn) == [
        ("proposal", "approved", "user", None),
        ("export", "approved", "user", None),
    ]
    assert actions(conn, f"proposal:{proposal_id}") == [
        ("agent", "propose"),
        ("user", "approve"),
        ("system", "apply"),
    ]
    # Each decision records where it was made.
    (approve,) = [
        e for e in audit_entries(conn, f"proposal:{proposal_id}") if e.action == "approve"
    ]
    assert approve.details["via"] == {"channel": "mcp", "client": "claude-code"}
    assert ("user", "approve_export") in actions(conn, f"deck:{deck_id}")
    assert ("system", "export") in actions(conn, f"deck:{deck_id}")


def test_a_rejection_is_a_decision_too(conn: psycopg.Connection, pool_id: UUID) -> None:
    state = session(conn)
    _, proposal_id = drafted(conn, pool_id, state)
    (shown,) = calls(state, ("show_proposal", {"proposal_id": proposal_id}))
    summary = confirming(shown).replace("Approve:", "Reject:", 1)

    (rejected,) = calls(
        state,
        (
            "reject_proposal",
            {"proposal_id": proposal_id, "confirming": summary, "reason": "Too many rats."},
        ),
    )

    assert not rejected.is_error, text(rejected)
    assert approvals(conn) == [("proposal", "rejected", "user", "Too many rats.")]


# --- what the model can't do ----------------------------------------------------------------


@MODES
def test_the_confirmation_must_match_the_servers_summary(
    conn: psycopg.Connection, pool_id: UUID, mode: str
) -> None:
    # The user allows what the prompt shows; the prompt shows the arguments.
    state = session(conn)
    _, proposal_id = drafted(conn, pool_id, state)

    (slanted,) = calls(
        state,
        (
            "approve_proposal",
            {"proposal_id": proposal_id, "confirming": "Approve: a quick fix, 3 cards"},
        ),
        mode=mode,
    )

    assert slanted.is_error
    assert f"Approve: {ATRAXA} as commander, 100 cards, for deck 'MCP'" in text(slanted)
    assert approvals(conn) == []


@MODES
def test_clients_not_known_to_ask_the_user_cant_decide(
    conn: psycopg.Connection, pool_id: UUID, mode: str
) -> None:
    state = session(conn)
    deck_id, proposal_id = drafted(conn, pool_id, state)
    (shown,) = calls(state, ("show_proposal", {"proposal_id": proposal_id}))

    approved, exported = calls(
        state,
        ("approve_proposal", {"proposal_id": proposal_id, "confirming": confirming(shown)}),
        ("approve_export", {"deck_id": deck_id, "confirming": "x"}),
        mode=mode,
        client="some-other-client",
    )

    for result in (approved, exported):
        assert result.is_error and "mtg-advisor" in text(result)
    assert approvals(conn) == []


def test_consent_cant_be_passed_as_an_argument(conn: psycopg.Connection, pool_id: UUID) -> None:
    state = session(conn)
    _, proposal_id = drafted(conn, pool_id, state)
    (shown,) = calls(state, ("show_proposal", {"proposal_id": proposal_id}))

    (result,) = calls(
        state,
        (
            "approve_proposal",
            {"proposal_id": proposal_id, "confirming": confirming(shown), "actor": "user"},
        ),
    )

    assert result.is_error and "Invalid arguments" in text(result)
    assert approvals(conn) == []


def test_steps_that_cant_happen_are_refused(conn: psycopg.Connection, pool_id: UUID) -> None:
    state = session(conn)
    deck_id, _ = drafted(conn, pool_id, state)

    unknown, malformed, unsaved_export, no_export = calls(
        state,
        (
            "approve_proposal",
            {"proposal_id": "00000000-0000-0000-0000-000000000000", "confirming": "x"},
        ),
        ("show_proposal", {"proposal_id": "nope"}),
        ("approve_export", {"deck_id": deck_id, "confirming": "x"}),  # nothing saved yet
        ("export_deck", {"deck_id": deck_id}),
    )

    assert unknown.is_error and "no proposal" in text(unknown)
    assert malformed.is_error
    assert unsaved_export.is_error and "nothing saved" in text(unsaved_export)
    assert no_export.is_error
    assert approvals(conn) == []
