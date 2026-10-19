"""The deck agent on the Claude Agent SDK, beside the hand-built loop (#153, AGT-6).

The SDK runs its own loop in a Claude Code subprocess. These tests cover what
the project controls: that it's locked down to the deck agent's own tools,
that those tools are the same code the hand-built loop calls, and how the
SDK's result becomes a run. A live comparison runs through the deck tasks.
"""

# Fixtures imported from other test modules are named again as test parameters,
# which is how pytest injects them; ruff reads that as a redefinition.
# ruff: noqa: F811

import asyncio
from pathlib import Path

import psycopg
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny, ResultMessage

from mtg_deck_advisor.agent.runs import tool_calls_for
from mtg_deck_advisor.agent.sdk import (
    SERVER,
    permission,
    run_status,
    sdk_options,
    sdk_tools,
)
from mtg_deck_advisor.agent.tools import tool_specs
from tests.integration.test_agent_flows import conn  # noqa: F401
from tests.integration.test_agent_tools import loaded, session_for  # noqa: F401


def result(subtype: str, **fields: object) -> ResultMessage:
    base: dict[str, object] = {
        "subtype": subtype,
        "duration_ms": 1000,
        "duration_api_ms": 900,
        "is_error": subtype != "success",
        "num_turns": 4,
        "session_id": "s",
        "total_cost_usd": 0.12,
        "result": "Drafted.",
    }
    return ResultMessage(**(base | fields))  # type: ignore[arg-type]


def test_the_sdk_is_locked_down_to_the_agents_own_tools(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    ctx = session_for(conn, "draft").context

    options = sdk_options(
        ctx,
        system="The system prompt.",
        model="claude-sonnet-5-5",
        max_turns=30,
        budget_usd=1.0,
        api_key="sk-ant-test",
        home=tmp_path,
    )

    assert options.tools == []  # no built-in tools: no shell, no files, no web
    assert options.setting_sources == []  # none of the user's settings or CLAUDE.md
    assert options.allowed_tools == [f"mcp__{SERVER}__{spec.name}" for spec in tool_specs("draft")]
    assert list(options.mcp_servers) == [SERVER]  # type: ignore[arg-type]
    assert options.system_prompt == "The system prompt."
    assert (options.model, options.max_turns, options.max_budget_usd) == (
        "claude-sonnet-5-5",
        30,
        1.0,
    )
    assert options.env["ANTHROPIC_API_KEY"] == "sk-ant-test"
    # Its own config directory and working directory, away from the user's.
    assert options.env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "config")
    assert options.cwd == tmp_path


def test_only_the_agents_tools_are_allowed(conn: psycopg.Connection) -> None:
    ctx = session_for(conn, "draft").context
    check = permission(ctx)

    async def ask(name: str) -> object:
        return await check(name, {}, None)  # type: ignore[arg-type]

    allowed = asyncio.run(ask(f"mcp__{SERVER}__search_pool"))
    shell = asyncio.run(ask("Bash"))
    other = asyncio.run(ask(f"mcp__{SERVER}__approve_proposal"))
    refine_only = asyncio.run(ask(f"mcp__{SERVER}__propose_changes"))

    assert isinstance(allowed, PermissionResultAllow)
    assert all(isinstance(r, PermissionResultDeny) for r in (shell, other, refine_only))


def test_each_sdk_tool_runs_the_same_code_as_the_hand_built_loop(
    conn: psycopg.Connection,
) -> None:
    session = session_for(conn, "draft")
    tools = {t.name: t for t in sdk_tools(session.context)}

    found = asyncio.run(tools["get_card"].handler({"name": "Sol Ring"}))
    missing = asyncio.run(tools["get_card"].handler({"name": "Not A Card"}))

    assert sorted(tools) == sorted(spec.name for spec in tool_specs("draft"))
    assert "Sol Ring" in found["content"][0]["text"] and not found.get("is_error")
    assert missing["is_error"] is True
    # Recorded under the run, like the hand-built loop's calls.
    recorded = tool_calls_for(conn, session.context.run_id)
    assert [(r.turn, r.tool, r.outcome) for r in recorded] == [
        (1, "get_card", "ok"),
        (2, "get_card", "error"),
    ]


def test_the_sdks_result_becomes_a_runs_status() -> None:
    assert run_status(result("success")) == "completed"
    assert run_status(result("error_max_turns")) == "turn_limit"
    assert run_status(result("error_max_budget_usd")) == "budget"
    assert run_status(result("error_during_execution")) == "error"
    assert run_status(None) == "error"  # the subprocess ended without a result
