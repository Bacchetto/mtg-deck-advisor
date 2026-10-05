"""The agent's tools as an MCP server (AGT-5). See ADR 0014.

An MCP client's own model (Claude Code, Claude Desktop) becomes the agent: it
gets the same six tools the built-in agent has, scoped by a `deck_id`, plus
`list_pools`, `list_decks` and `new_deck` to find its way. Every call goes
through `agent.tools.execute`, so it gets exactly the agent's guardrails:

- arguments validated against the same Pydantic models, with errors returned
  to the client as error results (AGT-4);
- pool and rule text delimited as `<untrusted>` (GRD-3);
- proposals checked against the Commander rules and the citations against
  what this session's tools returned, then left `pending` (GRD-1, RAG-3);
- every call recorded in `tool_calls` (OBS-1), under one `agent_runs` row per
  deck per session, with the model recorded as `mcp:<client>`.

There is no tool to approve, apply or export. A proposal made from an MCP
client waits for the user, in the CLI or the API, like any other (GRD-2).

A deck with a saved version is refined (`propose_changes`); one without is
drafted (`propose_deck`). Which one is decided when the session first touches
the deck, as for the agent's runs.
"""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import psycopg
from mcp import types
from mcp.server import Server, ServerRequestContext
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mtg_deck_advisor.agent.prompts import UNTRUSTED
from mtg_deck_advisor.agent.runs import Task, finish_run, record_progress, start_run
from mtg_deck_advisor.agent.tools import TOOLS, ToolContext, execute
from mtg_deck_advisor.deck.store import (
    Pool,
    create_deck,
    list_decks,
    list_pools,
    load_deck,
    load_pool,
)
from mtg_deck_advisor.guardrails.audit import record_audit
from mtg_deck_advisor.guardrails.untrusted import untrusted
from mtg_deck_advisor.llm.anthropic import strict_schema
from mtg_deck_advisor.llm.ollama import Embedder
from mtg_deck_advisor.llm.types import ToolCall
from mtg_deck_advisor.retrieval.rerank import Reranker

INSTRUCTIONS = f"""\
Build Magic: The Gathering Commander decks from a user's card pool, using only \
the cards they own. Start with list_pools, then new_deck to draft a new deck or \
list_decks to continue one. Research with search_pool, get_card and search_rules, \
check with analyze_deck, then propose: propose_deck for a deck with nothing saved, \
propose_changes for a saved one. Code checks every proposal against the Commander \
rules and returns every problem to fix. Nothing is saved until the user approves \
the proposal themselves, with the mtg-advisor CLI or the API; you can't approve, \
apply or export anything.

{UNTRUSTED}"""

DECK_ID = {
    "type": "string",
    "format": "uuid",
    "description": "The deck to work on, from list_decks or new_deck.",
}


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListPoolsArgs(Arguments):
    pass


class ListDecksArgs(Arguments):
    pool_id: UUID | None = Field(default=None, description="Only this pool's decks.")


class NewDeckArgs(Arguments):
    pool_id: UUID = Field(description="The pool to build from, from list_pools.")
    name: str = Field(min_length=1, max_length=200, description="A name for the deck.")


@dataclass
class McpSession:
    """One client's session: its connection and a tool context per deck it works on."""

    conn: psycopg.Connection
    embedder: Embedder
    reranker: Reranker[UUID] | None = None
    client_name: str = "client"
    _contexts: dict[UUID | None, ToolContext] = field(default_factory=dict)
    _turns: dict[UUID, int] = field(default_factory=dict)

    def tools(self) -> list[types.Tool]:
        own = [
            _tool("list_pools", "List the user's card pools, with their sizes.", ListPoolsArgs),
            _tool("list_decks", "List decks and their latest saved version.", ListDecksArgs),
            _tool(
                "new_deck",
                "Start a new, empty deck from a pool, to draft into with propose_deck.",
                NewDeckArgs,
            ),
        ]
        agent = []
        for tool in TOOLS.values():
            schema = strict_schema(tool.spec().input_schema)
            deck_id = dict(DECK_ID)
            required = schema.get("required", [])
            if tool.name == "search_rules":
                deck_id["description"] = (
                    "Optional: the deck these rules are for, so its proposals can cite them."
                )
            else:
                required = ["deck_id", *required]
            schema["properties"] = {"deck_id": deck_id, **schema["properties"]}
            schema["required"] = required
            agent.append(
                types.Tool(name=tool.name, description=tool.description, input_schema=schema)
            )
        return own + agent

    def call(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        """Run one tool call and commit what it did."""
        try:
            if name == "list_pools":
                return self._list_pools(ListPoolsArgs.model_validate(arguments))
            if name == "list_decks":
                return self._list_decks(ListDecksArgs.model_validate(arguments))
            if name == "new_deck":
                return self._new_deck(NewDeckArgs.model_validate(arguments))
            return self._agent_tool(name, dict(arguments))
        except ValidationError as exc:
            return _result(f"Invalid arguments for {name}: {_describe(exc)}.", error=True)
        finally:
            self.conn.commit()

    def close(self) -> None:
        """End the session's runs as completed, with the number of calls each made."""
        for ctx in self._contexts.values():
            finish_run(
                self.conn,
                ctx.run_id,
                status="completed",
                turns=self._turns[ctx.run_id],
                cost_usd=0.0,  # the client's model is billed to the client, not here
                final_text=None,
                transcript=[],
            )
        self.conn.commit()

    # --- tools -------------------------------------------------------------------------

    def _list_pools(self, args: ListPoolsArgs) -> types.CallToolResult:
        pools = list_pools(self.conn)
        if not pools:
            return _result(
                "There are no pools yet: the user adds them with `mtg-advisor pool add`."
            )
        lines = [f"{p.id}  {p.name}: {p.cards} cards ({p.distinct} different)" for p in pools]
        return _result(f"{len(pools)} pools:\n{untrusted(chr(10).join(lines))}")

    def _list_decks(self, args: ListDecksArgs) -> types.CallToolResult:
        decks = list_decks(self.conn, args.pool_id)
        if not decks:
            return _result("There are no decks yet: start one with new_deck.")
        lines = [
            f"{d.id}  {d.name} (pool {d.pool_id}): "
            + (f"version {d.version}" if d.version else "nothing saved yet")
            for d in decks
        ]
        return _result(f"{len(decks)} decks:\n{untrusted(chr(10).join(lines))}")

    def _new_deck(self, args: NewDeckArgs) -> types.CallToolResult:
        if load_pool(self.conn, args.pool_id) is None:
            return _result(f"There is no pool {args.pool_id}. Use list_pools.", error=True)
        deck_id = create_deck(self.conn, args.pool_id, name=args.name)
        record_audit(
            self.conn,
            "agent",
            "create_deck",
            f"deck:{deck_id}",
            {"pool": str(args.pool_id), "client": self.client_name},
        )
        return _result(f"Created deck {args.name!r}. Its deck_id is {deck_id}.")

    def _agent_tool(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        raw = arguments.pop("deck_id", None)
        try:
            deck_id = UUID(str(raw)) if raw is not None else None
        except ValueError:
            return _result(f"deck_id: {raw!r} is not a deck ID; use list_decks.", error=True)
        if deck_id is None and name != "search_rules":
            return _result(f"{name} needs a deck_id, from list_decks or new_deck.", error=True)
        ctx = self._context(deck_id)
        if isinstance(ctx, str):
            return _result(ctx, error=True)
        turn = self._turns[ctx.run_id] = self._turns[ctx.run_id] + 1
        result = execute(ctx, ToolCall(id=f"mcp-{turn}", name=name, arguments=arguments), turn=turn)
        record_progress(self.conn, ctx.run_id, turns=turn, cost_usd=0.0)
        return _result(result.content, error=result.is_error)

    def _context(self, deck_id: UUID | None) -> ToolContext | str:
        """The session's context for a deck (or for rules alone), started on first use."""
        if deck_id in self._contexts:
            return self._contexts[deck_id]
        model = f"mcp:{self.client_name}"
        if deck_id is None:
            run_id = start_run(self.conn, "rules", model)
            ctx = self._new_context(run_id, "rules", None, None)
        else:
            deck = load_deck(self.conn, deck_id)
            if deck is None:
                return (
                    f"There is no deck {deck_id}. Use list_decks to find one, or new_deck to "
                    "start one."
                )
            task: Task = "refine" if deck.version else "draft"
            pool = load_pool(self.conn, deck.pool_id)
            run_id = start_run(self.conn, task, model, pool_id=deck.pool_id, deck_id=deck_id)
            ctx = self._new_context(run_id, task, pool, deck_id)
        self._contexts[deck_id] = ctx
        self._turns[run_id] = 0
        return ctx

    def _new_context(
        self, run_id: UUID, task: Task, pool: Pool | None, deck_id: UUID | None
    ) -> ToolContext:
        return ToolContext(
            conn=self.conn,
            embedder=self.embedder,
            reranker=self.reranker,
            run_id=run_id,
            task=task,
            pool=pool,
            deck_id=deck_id,
        )


def build_server(session: McpSession) -> Server[Any]:
    """An MCP server (low-level API, so the schemas are exactly the agent's) for one session."""

    async def list_tools(
        ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=session.tools())

    async def call_tool(
        ctx: ServerRequestContext[Any], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        return session.call(params.name, params.arguments or {})

    return Server(
        "mtg-deck-advisor",
        version="0.1.0",
        instructions=INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


def _tool(name: str, description: str, args: type[BaseModel]) -> types.Tool:
    return types.Tool(
        name=name, description=description, input_schema=strict_schema(args.model_json_schema())
    )


def _result(text: str, *, error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=error)


def _describe(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in e['loc']) or 'arguments'}: {e['msg']}"
        for e in error.errors()
    )
