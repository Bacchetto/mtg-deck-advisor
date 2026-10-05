"""Renaming a deck, keeping everything else about it (#111).

A rename changes only the name: versions, proposals and runs stay as they
were. It's audited with the old and new names, as the user's through the API
and CLI and as the client's through MCP, which doesn't prompt for it. A name
the user chose is theirs, so applying the first version doesn't replace it.
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
from mtg_deck_advisor.deck.store import create_deck, load_deck
from mtg_deck_advisor.guardrails.audit import audit_entries
from tests.integration.test_agent_tools import loaded  # noqa: F401
from tests.integration.test_api_proposals import ok
from tests.integration.test_api_runs import (
    DRAFT_SCRIPT,
    add_pool,
    app_for,  # noqa: F401 (a fixture)
)
from tests.integration.test_cli import Cli
from tests.integration.test_deck_names import accept, draft, name
from tests.integration.test_mcp_approvals import calls
from tests.integration.test_mcp_server import conn, pool_id, session, text  # noqa: F401


def renames(settings: Settings, deck_id: str) -> list[tuple[str, dict[str, object]]]:
    with connect(settings) as conn:
        entries = audit_entries(conn, f"deck:{deck_id}")
    return [(e.actor, e.details) for e in entries if e.action == "rename"]


def test_the_user_renames_a_deck_and_keeps_its_history(
    app_for: Callable[..., TestClient], loaded: Settings
) -> None:
    client = app_for(*DRAFT_SCRIPT)
    deck_id, proposal_id = draft(client, add_pool(client))
    accept(client, proposal_id)
    before = client.get(f"/decks/{deck_id}").json()

    renamed = ok(client.patch(f"/decks/{deck_id}", json={"name": "  Rat pack "}))

    after = client.get(f"/decks/{deck_id}").json()
    assert renamed["name"] == after["name"] == "Rat pack"
    assert {**after, "name": before["name"]} == before
    assert renames(loaded, deck_id)[-1] == ("user", {"from": before["name"], "to": "Rat pack"})


def test_a_deck_renamed_before_its_first_version_keeps_the_users_name(
    app_for: Callable[..., TestClient],
) -> None:
    client = app_for(*DRAFT_SCRIPT)
    deck_id, proposal_id = draft(client, add_pool(client))

    ok(client.patch(f"/decks/{deck_id}", json={"name": "Rat pack"}))
    accept(client, proposal_id)

    assert name(client, deck_id) == "Rat pack"


def test_a_bad_rename_is_refused_and_changes_nothing(
    app_for: Callable[..., TestClient],
) -> None:
    client = app_for(*DRAFT_SCRIPT)
    deck_id, _ = draft(client, add_pool(client))

    unknown = client.patch(f"/decks/{uuid4()}", json={"name": "Rat pack"})
    empty = client.patch(f"/decks/{deck_id}", json={"name": "   "})
    long = client.patch(f"/decks/{deck_id}", json={"name": "x" * 201})

    assert unknown.status_code == 404
    assert (empty.status_code, long.status_code) == (422, 422)
    assert name(client, deck_id) == "New Binder deck"


def test_the_cli_renames_a_deck(app_for: Callable[..., TestClient], tmp_path: Path) -> None:
    cli = Cli(app_for(*DRAFT_SCRIPT), tmp_path)
    pool = cli.json("pool", "add", cli.pool_file(), "--name", "Binder")
    run = cli.json("draft", pool["pool_id"])

    code, out, _ = cli("rename", run["deck_id"], "Rat pack")

    assert code == 0 and "'New Binder deck' is now 'Rat pack'" in out
    assert cli.json("deck", run["deck_id"])["name"] == "Rat pack"


def test_mcp_renames_a_deck_as_the_clients_action(conn: psycopg.Connection, pool_id: UUID) -> None:
    deck_id = create_deck(conn, pool_id, name="Old")

    renamed, unknown = calls(
        session(conn),
        ("rename_deck", {"deck_id": str(deck_id), "name": "New"}),
        ("rename_deck", {"deck_id": str(uuid4()), "name": "New"}),
    )

    assert not renamed.is_error and "'Old' is now 'New'" in text(renamed)
    assert unknown.is_error
    deck = load_deck(conn, deck_id)
    assert deck is not None and deck.name == "New"
    (entry,) = [e for e in audit_entries(conn, f"deck:{deck_id}") if e.action == "rename"]
    assert (entry.actor, entry.details) == (
        "agent",
        {"from": "Old", "to": "New", "client": "claude-code"},
    )
