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
from typing import Any

import psycopg
import pytest
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny, ResultMessage

from mtg_deck_advisor.agent import flows, sdk
from mtg_deck_advisor.agent.runs import tool_calls_for
from mtg_deck_advisor.agent.sdk import (
    SERVER,
    permission,
    run_status,
    sdk_options,
    sdk_tools,
)
from mtg_deck_advisor.agent.tools import ToolContext, tool_specs
from mtg_deck_advisor.config import get_settings
from mtg_deck_advisor.evaluation.results import Variant
from mtg_deck_advisor.evaluation.runner import variant_services
from mtg_deck_advisor.llm.fake import FakeEmbedder, FakeProvider
from mtg_deck_advisor.llm.recording import MemoryRecorder
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
    assert options.effort == "medium"  # what the hand-built loop asks for
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

    async def get_card(name: str) -> dict[str, Any]:
        return await tools["get_card"].handler({"name": name})

    found = asyncio.run(get_card("Sol Ring"))
    missing = asyncio.run(get_card("Not A Card"))

    assert sorted(tools) == sorted(spec.name for spec in tool_specs("draft"))
    assert "Sol Ring" in found["content"][0]["text"] and not found.get("is_error")
    assert missing["is_error"] is True
    # Recorded under the run, like the hand-built loop's calls.
    recorded = tool_calls_for(conn, session.context.run_id)
    assert [(r.turn, r.tool, r.outcome) for r in recorded] == [
        (1, "get_card", "ok"),
        (2, "get_card", "error"),
    ]


def test_an_sdk_variant_runs_its_flows_on_the_sdk(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = variant_services(
        conn,
        Variant(name="sonnet-sdk", model="claude-sonnet-5-5", harness="sdk"),
        provider=FakeProvider(),
        embedder=FakeEmbedder(),
        reranker=None,
        recorder=MemoryRecorder(),
        cost_cap_usd=0.75,
        max_turns=12,
    )
    ctx = session_for(conn, "draft").context
    seen: dict[str, Any] = {}

    ran_on_sdk: Any = object()

    def fake_sdk_agent(ctx: ToolContext, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return ran_on_sdk

    monkeypatch.setattr(sdk, "run_sdk_agent", fake_sdk_agent)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    get_settings.cache_clear()
    try:
        ran = flows._run(services, ctx, "Draft a deck.")
    finally:
        get_settings.cache_clear()

    assert services.harness == "sdk"
    assert ran is ran_on_sdk
    # The same prompt, model, turn limit and cost cap as the hand-built loop.
    assert seen == {
        "system": services.system("draft"),
        "prompt": "Draft a deck.",
        "model": "claude-sonnet-5-5",
        "max_turns": 12,
        "budget_usd": 0.75,
        "api_key": "sk-ant-test",
    }


def test_the_sdks_result_becomes_a_runs_status() -> None:
    assert run_status(result("success")) == "completed"
    assert run_status(result("error_max_turns")) == "turn_limit"
    assert run_status(result("error_max_budget_usd")) == "budget"
    assert run_status(result("error_during_execution")) == "error"
    assert run_status(None) == "error"  # the subprocess ended without a result
