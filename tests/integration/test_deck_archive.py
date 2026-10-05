"""Archiving decks: hidden from lists, history kept, nothing changed until unarchived (#112).

Nothing is deleted: versions, proposals, runs and audit entries all stay,
and an archived deck can still be read. It can't change or leave the system
while archived. Through MCP, archiving is a decision the user allows in
Claude Code's permission prompt, like an approval; unarchiving hides
nothing, so it isn't.
"""

# Fixtures imported from other test modules are named again as test parameters,
# which is how pytest injects them; ruff reads that as a redefinition.
# ruff: noqa: F811

from collections.abc import Callable
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from fastapi.testclient import TestClient

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.deck.store import create_deck, list_decks
from mtg_deck_advisor.guardrails.audit import audit_entries
from tests.integration.test_agent_tools import loaded  # noqa: F401
from tests.integration.test_api_proposals import ok
from tests.integration.test_api_runs import (
    DRAFT_SCRIPT,
    add_pool,
    app_for,  # noqa: F401 (a fixture)
)
from tests.integration.test_cli import Cli
from tests.integration.test_deck_names import accept, draft
from tests.integration.test_mcp_approvals import approvals, calls, drafted
from tests.integration.test_mcp_server import conn, pool_id, session, text  # noqa: F401


def listed(client: TestClient, **params: str) -> list[str]:
    return [deck["id"] for deck in ok(client.get("/decks", params=params))]


def deck_actions(settings: Settings, deck_id: str) -> list[tuple[str, str]]:
    with connect(settings) as conn:
        return [(e.actor, e.action) for e in audit_entries(conn, f"deck:{deck_id}")]


# --- the API -------------------------------------------------------------------------------


def test_an_archived_deck_leaves_the_list_but_keeps_its_history(
    app_for: Callable[..., TestClient], loaded: Settings
) -> None:
    client = app_for(*DRAFT_SCRIPT, *DRAFT_SCRIPT)
    pool = add_pool(client)
    kept, _ = draft(client, pool)
    archived, proposal_id = draft(client, pool)
    accept(client, proposal_id)
    before = ok(client.get(f"/decks/{archived}"))

    result = ok(client.post(f"/decks/{archived}/archive"))

    assert result["archived"] is True
    assert listed(client) == [kept]
    assert listed(client, include_archived="true") == [kept, archived]
    assert listed(client, pool_id=pool) == [kept]
    after = ok(client.get(f"/decks/{archived}"))
    assert after["archived"] is True and before["archived"] is False
    assert {**after, "archived": False} == before  # versions and decklist untouched
    assert ok(client.get(f"/proposals/{proposal_id}"))["status"] == "applied"
    assert ("user", "archive") in deck_actions(loaded, archived)


def test_an_archived_deck_cant_change_or_be_exported(app_for: Callable[..., TestClient]) -> None:
    client = app_for(*DRAFT_SCRIPT, *DRAFT_SCRIPT, *DRAFT_SCRIPT)
    pool = add_pool(client)
    saved, saved_proposal = draft(client, pool)
    accept(client, saved_proposal)
    ok(client.post(f"/decks/{saved}/versions/1/export-approval", json={}))
    pending, pending_proposal = draft(client, pool)
    approved, approved_proposal = draft(client, pool)
    ok(client.post(f"/proposals/{approved_proposal}/approval", json={}))
    for deck in (saved, pending, approved):
        ok(client.post(f"/decks/{deck}/archive"))

    refused = {
        "refine": client.post(f"/decks/{saved}/refinements", json={"request": "More rats."}),
        "export approval": client.post(f"/decks/{saved}/versions/1/export-approval", json={}),
        "export": client.get(f"/decks/{saved}/versions/1/export"),
        "approve": client.post(f"/proposals/{pending_proposal}/approval", json={}),
        "reject": client.post(f"/proposals/{pending_proposal}/rejection", json={"reason": "No."}),
        "apply": client.post(f"/proposals/{approved_proposal}/application"),
    }

    for step, response in refused.items():
        assert response.status_code == 409, (step, response.text)
        assert "archived" in response.json()["detail"], step
    assert ok(client.get(f"/proposals/{pending_proposal}"))["status"] == "pending"
    assert ok(client.get(f"/decks/{approved}"))["version"] is None


def test_unarchiving_brings_a_deck_back_as_it_was(
    app_for: Callable[..., TestClient], loaded: Settings
) -> None:
    client = app_for(*DRAFT_SCRIPT)
    deck_id, proposal_id = draft(client, add_pool(client))
    ok(client.post(f"/decks/{deck_id}/archive"))

    result = ok(client.post(f"/decks/{deck_id}/unarchive"))
    accept(client, proposal_id)

    assert result["archived"] is False
    assert listed(client) == [deck_id]
    assert ok(client.get(f"/decks/{deck_id}"))["version"] == 1
    actions = [action for _, action in deck_actions(loaded, deck_id)]
    assert actions[:2] == ["archive", "unarchive"]


def test_archiving_twice_or_an_unknown_deck_is_refused(
    app_for: Callable[..., TestClient],
) -> None:
    client = app_for(*DRAFT_SCRIPT)
    deck_id, _ = draft(client, add_pool(client))
    ok(client.post(f"/decks/{deck_id}/archive"))

    assert client.post(f"/decks/{deck_id}/archive").status_code == 409
    assert client.post(f"/decks/{uuid4()}/archive").status_code == 404
    ok(client.post(f"/decks/{deck_id}/unarchive"))
    assert client.post(f"/decks/{deck_id}/unarchive").status_code == 409


# --- the CLI -------------------------------------------------------------------------------


def test_the_cli_lists_archives_and_unarchives_decks(
    app_for: Callable[..., TestClient], tmp_path: Path
) -> None:
    cli = Cli(app_for(*DRAFT_SCRIPT), tmp_path)
    pool = cli.json("pool", "add", cli.pool_file(), "--name", "Binder")
    deck_id = cli.json("draft", pool["pool_id"])["deck_id"]

    _, shown, _ = cli("decks")
    code, archived, _ = cli("archive", deck_id)
    _, hidden, _ = cli("decks")
    _, everything, _ = cli("decks", "--archived")
    cli("unarchive", deck_id)

    assert deck_id in shown and "New Binder deck" in shown
    assert code == 0 and "archived" in archived
    assert deck_id not in hidden
    assert deck_id in everything and "(archived)" in everything
    assert [deck["id"] for deck in cli.json("decks")] == [deck_id]


# --- MCP -----------------------------------------------------------------------------------


def test_through_mcp_archiving_is_the_users_decision(
    conn: psycopg.Connection, pool_id: UUID
) -> None:
    deck_id, _ = drafted(conn, pool_id, session(conn))

    slanted, archived, listing, everything = calls(
        session(conn),
        ("archive_deck", {"deck_id": deck_id, "confirming": "Archive deck 'Old junk'"}),
        ("archive_deck", {"deck_id": deck_id, "confirming": "Archive deck 'MCP'"}),
        ("list_decks", {}),
        ("list_decks", {"include_archived": True}),
    )

    assert slanted.is_error and "Archive deck 'MCP'" in text(slanted)
    assert not archived.is_error, text(archived)
    assert deck_id not in text(listing)
    assert deck_id in text(everything) and "archived" in text(everything)
    (entry,) = [e for e in audit_entries(conn, f"deck:{deck_id}") if e.action == "archive"]
    assert entry.actor == "user"
    assert entry.details["via"] == {"channel": "mcp", "client": "claude-code"}
    assert approvals(conn) == []  # archiving isn't an approval of anything


def test_through_mcp_an_archived_deck_cant_change(conn: psycopg.Connection, pool_id: UUID) -> None:
    state = session(conn)
    deck_id, proposal_id = drafted(conn, pool_id, state)
    (shown,) = calls(state, ("show_proposal", {"proposal_id": proposal_id}))
    calls(state, ("archive_deck", {"deck_id": deck_id, "confirming": "Archive deck 'MCP'"}))
    summary = text(shown).rsplit('confirming: "', 1)[1].rstrip('"')

    analysed, approved = calls(
        state,
        ("analyze_deck", {"deck_id": deck_id}),
        ("approve_proposal", {"proposal_id": proposal_id, "confirming": summary}),
    )

    assert analysed.is_error and "archived" in text(analysed)
    assert approved.is_error and "archived" in text(approved)
    assert approvals(conn) == []


def test_through_mcp_unarchiving_needs_no_prompt(conn: psycopg.Connection, pool_id: UUID) -> None:
    deck_id = create_deck(conn, pool_id, name="Old")
    calls(
        session(conn),
        ("archive_deck", {"deck_id": str(deck_id), "confirming": "Archive deck 'Old'"}),
    )

    (unarchived,) = calls(
        session(conn), ("unarchive_deck", {"deck_id": str(deck_id)}), client="some-other-client"
    )

    assert not unarchived.is_error, text(unarchived)
    assert [d.id for d in list_decks(conn, pool_id)] == [deck_id]
    (entry,) = [e for e in audit_entries(conn, f"deck:{deck_id}") if e.action == "unarchive"]
    assert (entry.actor, entry.details) == ("agent", {"client": "some-other-client"})
