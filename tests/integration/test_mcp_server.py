"""The agent's tools as an MCP server, driven by an in-process MCP client (#102, #107; AGT-5)."""

# Fixtures imported from other test modules are named again as test parameters,
# which is how pytest injects them; ruff reads that as a redefinition.
# ruff: noqa: F811

from collections.abc import Iterator
from typing import Any
from uuid import UUID

import anyio
import psycopg
import pytest
from mcp import Client, types

from mtg_deck_advisor.agent.runs import tool_calls_for
from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.deck.state import DeckState
from mtg_deck_advisor.deck.store import create_deck, create_pool, save_version
from mtg_deck_advisor.guardrails.audit import audit_entries
from mtg_deck_advisor.llm.fake import FakeEmbedder
from mtg_deck_advisor.mcp_server.server import McpSession, build_server
from tests.integration.test_agent_tools import (
    ATRAXA,
    BASICS,
    LEGAL_CARDS,
    POOL,
    RATS,
    card_id,
    loaded,  # noqa: F401 (a fixture)
)

AGENT_TOOLS = [
    "search_pool",
    "get_card",
    "search_rules",
    "analyze_deck",
    "propose_deck",
    "propose_changes",
]
USER_STEPS = [
    "list_proposals",
    "show_proposal",
    "approve_proposal",
    "reject_proposal",
    "apply_proposal",
    "approve_export",
    "export_deck",
]


@pytest.fixture
def conn(loaded: Settings) -> Iterator[psycopg.Connection]:
    with connect(loaded) as connection:
        yield connection


@pytest.fixture
def pool_id(conn: psycopg.Connection) -> UUID:
    pool = create_pool(conn, {card_id(n): c for n, c in POOL.items()}, name="Binder", source="text")
    conn.commit()
    return pool


def session(conn: psycopg.Connection) -> McpSession:
    return McpSession(conn=conn, embedder=FakeEmbedder(), client_name="test")


def calls(state: McpSession, *steps: tuple[str, dict[str, Any]]) -> list[types.CallToolResult]:
    """Run tool calls in order through a real MCP client session."""

    async def go() -> list[types.CallToolResult]:
        async with Client(build_server(state)) as client:
            return [await client.call_tool(name, arguments) for name, arguments in steps]

    return anyio.run(go)


def text(result: types.CallToolResult) -> str:
    (content,) = result.content
    assert isinstance(content, types.TextContent)
    return content.text


# --- the tool list --------------------------------------------------------------------


def test_the_tools_are_the_agents_own_scoped_by_deck_plus_the_users_steps(
    conn: psycopg.Connection,
) -> None:
    async def go() -> list[types.Tool]:
        async with Client(build_server(session(conn))) as client:
            return (await client.list_tools()).tools

    tools = {tool.name: tool for tool in anyio.run(go)}

    assert list(tools) == ["list_pools", "list_decks", "new_deck", *AGENT_TOOLS, *USER_STEPS]
    # Consent comes from the user's answer to a form, never from an argument.
    for name in ("approve_proposal", "reject_proposal", "approve_export"):
        properties = set(tools[name].input_schema["properties"])
        assert not properties & {"confirmed", "approved", "consent", "actor", "user"}, name
    search = tools["search_pool"].input_schema
    assert search["required"] == ["deck_id", "query"]
    assert "maximum 40" in search["properties"]["k"]["description"]
    assert "deck_id" not in tools["search_rules"].input_schema.get("required", [])
    assert all(tool.description for tool in tools.values())


# --- a deck built from an MCP client --------------------------------------------------


def test_a_client_can_find_a_pool_start_a_deck_research_and_propose(
    conn: psycopg.Connection, pool_id: UUID
) -> None:
    state = session(conn)

    pools, created = calls(
        state,
        ("list_pools", {}),
        ("new_deck", {"pool_id": str(pool_id), "name": "From Claude"}),
    )
    deck_id = text(created).split()[-1].rstrip(".")
    listed, search, rules, proposal = calls(
        state,
        ("list_decks", {"pool_id": str(pool_id)}),
        ("search_pool", {"deck_id": deck_id, "query": "creature", "k": 5}),
        ("search_rules", {"deck_id": deck_id, "question": "color identity commander", "k": 3}),
        (
            "propose_deck",
            {
                "deck_id": deck_id,
                "commander": ATRAXA,
                "cards": LEGAL_CARDS,
                "rationale": "Rats.",
                "citations": ["903.4"],
            },
        ),
    )

    assert str(pool_id) in text(pools) and "Binder" in text(pools)
    assert deck_id in text(listed) and "From Claude" in text(listed)
    assert not search.is_error and "<untrusted>" in text(search)
    assert not rules.is_error and "903.4" in text(rules)
    assert not proposal.is_error, text(proposal)
    assert "waiting for the user's approval" in text(proposal)
    # The client gets the proposal's ID, to show, approve or apply it.
    row = conn.execute("SELECT id FROM proposals WHERE deck_id = %s", (deck_id,)).fetchone()
    assert row is not None and f"proposal_id is {row[0]}" in text(proposal)
    # The proposal waits for the user, exactly like the agent's.
    status = conn.execute("SELECT status FROM proposals WHERE deck_id = %s", (deck_id,)).fetchone()
    assert status == ("pending",)
    assert conn.execute("SELECT count(*) FROM approvals").fetchone() == (0,)
    assert ("agent", "create_deck") in [
        (e.actor, e.action) for e in audit_entries(conn, f"deck:{deck_id}")
    ]


def test_calls_are_recorded_under_a_run_for_the_client(
    conn: psycopg.Connection, pool_id: UUID
) -> None:
    deck_id = create_deck(conn, pool_id, name="D")
    conn.commit()
    state = session(conn)

    calls(
        state,
        ("get_card", {"deck_id": str(deck_id), "name": "Sol Ring"}),
        ("get_card", {"deck_id": str(deck_id), "name": "Nope"}),
    )
    state.close()

    run_id, task, model, status, turns = (
        conn.execute(
            "SELECT id, task, model, status, turns FROM agent_runs WHERE deck_id = %s", (deck_id,)
        ).fetchone()
        or (None,) * 5
    )
    assert (task, model, status, turns) == ("draft", "mcp:test", "completed", 2)
    recorded = tool_calls_for(conn, run_id)
    assert [(r.turn, r.tool, r.outcome) for r in recorded] == [
        (1, "get_card", "ok"),
        (2, "get_card", "error"),
    ]
    assert recorded[0].arguments == {"name": "Sol Ring"}  # deck_id is the scope, not an argument


def test_a_saved_deck_is_refined_with_changes_not_redrafted(
    conn: psycopg.Connection, pool_id: UUID
) -> None:
    deck_id = create_deck(conn, pool_id, name="D")
    basics = {card_id(name): count for name, count in BASICS.items()}
    save_version(
        conn,
        deck_id,
        DeckState.new(
            card_id(ATRAXA),
            {card_id(RATS): 60, card_id("Sol Ring"): 1, card_id("Lim-Dûl's Cohort"): 1, **basics},
        ),
    )
    conn.commit()

    redraft, change = calls(
        session(conn),
        (
            "propose_deck",
            {"deck_id": str(deck_id), "commander": ATRAXA, "cards": [], "rationale": "r"},
        ),
        (
            "propose_changes",
            {
                "deck_id": str(deck_id),
                "add": [{"name": "Delver of Secrets"}],
                "remove": [{"name": "Island"}],
                "rationale": "A flier.",
            },
        ),
    )

    assert redraft.is_error and "isn't available" in text(redraft)
    assert not change.is_error, text(change)


# --- errors go back to the client --------------------------------------------------------


def test_bad_scopes_and_arguments_are_error_results(
    conn: psycopg.Connection, pool_id: UUID
) -> None:
    deck_id = create_deck(conn, pool_id, name="D")
    conn.commit()

    unknown, malformed, bad_args, no_pool = calls(
        session(conn),
        ("analyze_deck", {"deck_id": "00000000-0000-0000-0000-000000000000"}),
        ("get_card", {"deck_id": "not-a-uuid", "name": "Sol Ring"}),
        ("search_pool", {"deck_id": str(deck_id), "k": 5}),
        ("new_deck", {"pool_id": "00000000-0000-0000-0000-000000000000", "name": "x"}),
    )

    assert unknown.is_error and "no deck" in text(unknown) and "new_deck" in text(unknown)
    assert malformed.is_error and "deck_id" in text(malformed)
    assert bad_args.is_error and "query" in text(bad_args)
    assert no_pool.is_error and "no pool" in text(no_pool)
