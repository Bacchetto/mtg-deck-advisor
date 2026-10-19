"""The deck agent on the Claude Agent SDK, to compare with the hand-built loop (#153, AGT-6).

The hand-built loop (`agent.loop`) owns every turn: it calls the model through
the project's ModelClient, so every call is costed, capped before it's made,
recorded and replayable. Here the SDK owns the loop instead: it runs Claude
Code in a subprocess, which calls the model and the tools itself.

What stays the same is everything the loop doesn't own:
- **The tools are the same code.** Each is the deck agent's own tool, served
  from an in-process MCP server that calls the same `execute()` the loop
  calls, so validation, guardrails, proposals and tool-call recording are
  identical.
- **The same prompt, model, turn limit and cost cap.**

It's locked down, since Claude Code is a general agent: no built-in tools (no
shell, files or web), none of the user's settings or CLAUDE.md, its own config
and working directories, and a permission callback that allows only the
task's own tools.

What it gives up, which the comparison is about: the model calls don't go
through the ModelClient, so they aren't recorded in model_calls, can't be
replayed offline, and are capped by the SDK's own budget check.
"""

import asyncio
import json
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    SdkMcpTool,
    ToolPermissionContext,
    create_sdk_mcp_server,
    query,
    tool,
)

from mtg_deck_advisor.agent.loop import RunResult
from mtg_deck_advisor.agent.runs import RunStatus, finish_run
from mtg_deck_advisor.agent.tools import TOOLS, ToolContext, execute
from mtg_deck_advisor.llm.types import ToolCall

# The in-process MCP server's name: Claude Code calls its tools mcp__deck__<tool>.
SERVER = "deck"

type Permission = Callable[
    [str, dict[str, Any], ToolPermissionContext],
    Awaitable[PermissionResultAllow | PermissionResultDeny],
]


def _task_tools(ctx: ToolContext) -> list[str]:
    return [name for name, t in TOOLS.items() if ctx.task in t.tasks]


def sdk_tools(ctx: ToolContext) -> list[SdkMcpTool[Any]]:
    """The task's tools as SDK tools, each running the deck agent's own code."""
    turns = iter(range(1, 10_000))

    def adapt(name: str) -> SdkMcpTool[Any]:
        spec = TOOLS[name].spec()

        async def handler(arguments: dict[str, Any]) -> dict[str, Any]:
            call = ToolCall(id=f"toolu_{uuid.uuid4().hex[:12]}", name=name, arguments=arguments)
            result = execute(ctx, call, turn=next(turns))
            ctx.conn.commit()  # each call recorded as it happens, as in the loop
            answer: dict[str, Any] = {"content": [{"type": "text", "text": result.content}]}
            if result.is_error:
                answer["is_error"] = True
            return answer

        return tool(spec.name, spec.description, spec.input_schema)(handler)

    return [adapt(name) for name in _task_tools(ctx)]


def permission(ctx: ToolContext) -> Permission:
    """Allow the task's own tools and nothing else, whatever the model asks for."""
    allowed = {f"mcp__{SERVER}__{name}" for name in _task_tools(ctx)}

    async def check(
        name: str, arguments: dict[str, Any], context: ToolPermissionContext
    ) -> PermissionResultAllow | PermissionResultDeny:
        if name in allowed:
            return PermissionResultAllow()
        return PermissionResultDeny(message=f"{name} isn't available: use the deck tools only.")

    return check


def sdk_options(
    ctx: ToolContext,
    *,
    system: str,
    model: str,
    max_turns: int,
    budget_usd: float | None,
    api_key: str,
    home: Path,
) -> ClaudeAgentOptions:
    """The SDK's options for one run: the deck agent's tools, prompt and limits, locked down."""
    (home / "config").mkdir(parents=True, exist_ok=True)
    return ClaudeAgentOptions(
        tools=[],
        allowed_tools=[f"mcp__{SERVER}__{name}" for name in _task_tools(ctx)],
        mcp_servers={SERVER: create_sdk_mcp_server(SERVER, tools=sdk_tools(ctx))},
        can_use_tool=permission(ctx),
        system_prompt=system,
        model=model,
        max_turns=max_turns,
        max_budget_usd=budget_usd,
        setting_sources=[],
        cwd=home,
        env={"ANTHROPIC_API_KEY": api_key, "CLAUDE_CONFIG_DIR": str(home / "config")},
    )


def run_status(result: ResultMessage | None) -> RunStatus:
    """The run's status from the SDK's result."""
    if result is None:
        return "error"
    if result.subtype == "success":
        return "completed"
    if "max_turns" in result.subtype:
        return "turn_limit"
    if "budget" in result.subtype:
        return "budget"
    return "error"


def _jsonable(message: Any) -> dict[str, Any]:
    """An SDK message, as far as JSON can hold it, for the run's transcript."""
    data = getattr(message, "__dict__", {"value": str(message)})
    jsonable: dict[str, Any] = json.loads(
        json.dumps({"type": type(message).__name__, **data}, default=str)
    )
    return jsonable


async def _drive(
    prompt: str, options: ClaudeAgentOptions
) -> tuple[ResultMessage | None, list[Any]]:
    # Claude Code needs the prompt as a stream when the in-process MCP server and
    # the permission callback are in use.
    async def messages() -> Any:
        yield {"type": "user", "message": {"role": "user", "content": prompt}}

    seen: list[Any] = []
    result: ResultMessage | None = None
    async for message in query(prompt=messages(), options=options):
        seen.append(message)
        if isinstance(message, ResultMessage):
            result = message
    return result, seen


def run_sdk_agent(
    ctx: ToolContext,
    *,
    system: str,
    prompt: str,
    model: str,
    max_turns: int,
    budget_usd: float | None,
    api_key: str,
) -> RunResult:
    """Run the deck agent on the SDK, and record the run as the hand-built loop does."""
    error: str | None = None
    with tempfile.TemporaryDirectory(prefix="mtg-sdk-") as home:
        options = sdk_options(
            ctx,
            system=system,
            model=model,
            max_turns=max_turns,
            budget_usd=budget_usd,
            api_key=api_key,
            home=Path(home),
        )
        try:
            result, seen = asyncio.run(_drive(prompt, options))
        except Exception as exc:  # the subprocess failing is an outcome to record
            result, seen, error = None, [], repr(exc)
    status = run_status(result)
    if result is not None and status != "completed":
        error = "; ".join(result.errors or []) or result.subtype
    turns = result.num_turns if result else 0
    cost = (result.total_cost_usd or 0.0) if result else 0.0
    final_text = result.result if result and status == "completed" else None
    finish_run(
        ctx.conn,
        ctx.run_id,
        status=status,
        turns=turns,
        cost_usd=cost,
        final_text=final_text,
        transcript=[_jsonable(message) for message in seen],
        error=error,
    )
    ctx.conn.commit()
    return RunResult(
        run_id=ctx.run_id,
        status=status,
        turns=turns,
        cost_usd=cost,
        final_text=final_text,
        proposals=list(ctx.proposals),
        draft=ctx.draft,
        transcript=(),
        error=error,
    )
