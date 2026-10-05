"""Deck names: optional when drafting, and descriptive and unique by default (#110).

A deck the user doesn't name starts as "New <pool> deck"; when its first
version is saved it takes its commander's name. Default names never repeat
an existing deck's name, and a name the user chose is never changed for them.
"""

# Fixtures imported from other test modules are named again as test parameters,
# which is how pytest injects them; ruff reads that as a redefinition.
# ruff: noqa: F811

from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import anyio
import psycopg
from fastapi.testclient import TestClient
from mcp import Client

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.deck.store import create_deck, load_deck
from mtg_deck_advisor.guardrails.audit import audit_entries
from mtg_deck_advisor.mcp_server.server import build_server
from tests.integration.test_agent_tools import ATRAXA, loaded  # noqa: F401
from tests.integration.test_api_proposals import ok
from tests.integration.test_api_runs import (
    DRAFT_SCRIPT,
    add_pool,
    app_for,  # noqa: F401 (a fixture)
)
from tests.integration.test_cli import Cli
from tests.integration.test_mcp_server import conn, pool_id, session, text  # noqa: F401


def draft(client: TestClient, pool_id: str, **body: str) -> tuple[str, str]:
    """Start a draft and return its deck and its (one) proposal."""
    started = ok(client.post(f"/pools/{pool_id}/drafts", json=body), 202)
    (proposal,) = client.get(f"/runs/{started['run_id']}").json()["proposals"]
    return str(started["deck_id"]), str(proposal["id"])


def accept(client: TestClient, proposal_id: str) -> None:
    ok(client.post(f"/proposals/{proposal_id}/approval", json={}))
    ok(client.post(f"/proposals/{proposal_id}/application"))


def name(client: TestClient, deck_id: str) -> str:
    return str(client.get(f"/decks/{deck_id}").json()["name"])


# --- defaults --------------------------------------------------------------------------


def test_an_unnamed_deck_is_named_for_its_pool_until_it_has_a_commander(
    app_for: Callable[..., TestClient], loaded: Settings
) -> None:
    client = app_for(*DRAFT_SCRIPT)
    deck_id, proposal_id = draft(client, add_pool(client))

    before = name(client, deck_id)
    accept(client, proposal_id)

    assert before == "New Binder deck"
    assert name(client, deck_id) == ATRAXA
    with connect(loaded) as conn:
        (renamed,) = [e for e in audit_entries(conn, f"deck:{deck_id}") if e.action == "rename"]
    assert renamed.actor == "system"
    assert renamed.details == {"from": "New Binder deck", "to": ATRAXA, "reason": "commander"}


def test_default_names_dont_repeat_an_existing_decks_name(
    app_for: Callable[..., TestClient],
) -> None:
    client = app_for(*DRAFT_SCRIPT, *DRAFT_SCRIPT, *DRAFT_SCRIPT)
    pool = add_pool(client)
    first, first_proposal = draft(client, pool)
    second, second_proposal = draft(client, pool)
    third, _ = draft(client, pool)

    assert [name(client, deck) for deck in (first, second, third)] == [
        "New Binder deck",
        "New Binder deck (2)",
        "New Binder deck (3)",
    ]
    accept(client, first_proposal)
    accept(client, second_proposal)
    assert [name(client, deck) for deck in (first, second)] == [ATRAXA, f"{ATRAXA} (2)"]


def test_a_name_the_user_gave_is_kept(app_for: Callable[..., TestClient]) -> None:
    client = app_for(*DRAFT_SCRIPT, *DRAFT_SCRIPT)
    pool = add_pool(client)
    deck_id, proposal_id = draft(client, pool, name="Rat pack")
    # The user's names aren't changed to avoid a clash, either.
    twin, _ = draft(client, pool, name="Rat pack")

    accept(client, proposal_id)

    assert name(client, deck_id) == "Rat pack"
    assert name(client, twin) == "Rat pack"


def test_decks_made_in_code_are_named_the_same_way(conn: psycopg.Connection, pool_id: UUID) -> None:
    named = create_deck(conn, pool_id, name="Draft")
    unnamed = create_deck(conn, pool_id)

    assert [deck.name for deck in (load_deck(conn, named), load_deck(conn, unnamed)) if deck] == [
        "Draft",
        "New Binder deck",
    ]


def test_a_draft_name_is_checked(app_for: Callable[..., TestClient]) -> None:
    client = app_for()
    pool = add_pool(client)

    assert client.post(f"/pools/{pool}/drafts", json={"name": ""}).status_code == 422
    assert client.post(f"/pools/{pool}/drafts", json={"name": "x" * 201}).status_code == 422


# --- the CLI and MCP ---------------------------------------------------------------------


def test_the_cli_drafts_with_a_name(app_for: Callable[..., TestClient], tmp_path: Path) -> None:
    cli = Cli(app_for(*DRAFT_SCRIPT), tmp_path)
    pool = cli.json("pool", "add", cli.pool_file(), "--name", "Binder")

    run = cli.json("draft", pool["pool_id"], "--name", "Rat pack")

    assert cli.json("deck", run["deck_id"])["name"] == "Rat pack"


def test_mcp_new_deck_names_it_by_default(conn: psycopg.Connection, pool_id: UUID) -> None:
    async def go() -> list[str]:
        async with Client(build_server(session(conn))) as client:
            named = await client.call_tool("new_deck", {"pool_id": str(pool_id), "name": "Mine"})
            unnamed = await client.call_tool("new_deck", {"pool_id": str(pool_id)})
            return [text(named), text(unnamed)]

    named, unnamed = anyio.run(go)

    assert "Created deck 'Mine'" in named
    assert "Created deck 'New Binder deck'" in unnamed
