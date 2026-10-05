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

The user's own steps are tools too, so a deck can be built from draft to
export without leaving the client: `list_proposals`, `show_proposal`,
`apply_proposal` and `export_deck`, and the decisions `approve_proposal`,
`reject_proposal` and `approve_export`. A decision is the user's, not the
model's. Each decision tool is marked as requiring user interaction
(`_meta["anthropic/requiresUserInteraction"]`), so Claude Code always shows
the user its Allow/Deny prompt for it: no allow rule, permission mode or hook
can approve it for them. The prompt shows the call's arguments, and the
`confirming` argument must be the server's own summary of the decision, word
for word, so what the user allows can't be slanted by the model. Only
clients known to honour the marking may decide (`MCP_DECISION_CLIENTS`);
others are sent to the CLI. Apply and export still refuse without the
recorded approval (GRD-2, GRD-5). See ADR 0015.

A deck with a saved version is refined (`propose_changes`); one without is
drafted (`propose_deck`). Which one is decided when the session first touches
the deck, as for the agent's runs.
"""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import psycopg
import structlog
from mcp import types
from mcp.server import Server, ServerRequestContext
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mtg_deck_advisor.agent.prompts import UNTRUSTED
from mtg_deck_advisor.agent.runs import Task, finish_run, record_progress, start_run
from mtg_deck_advisor.agent.tools import TOOLS, ToolContext, execute
from mtg_deck_advisor.deck.state import DeckState
from mtg_deck_advisor.deck.store import (
    DeckName,
    Pool,
    card_names,
    create_deck,
    decklist,
    list_decks,
    list_pools,
    load_deck,
    load_pool,
    rename_deck,
)
from mtg_deck_advisor.guardrails.approvals import (
    ApprovalError,
    apply_proposal,
    approve_export,
    approve_proposal,
    export_deck,
    reject_proposal,
)
from mtg_deck_advisor.guardrails.audit import record_audit
from mtg_deck_advisor.guardrails.proposals import (
    ProposalRecord,
    deck_proposals,
    load_proposal,
    named_changes,
)
from mtg_deck_advisor.guardrails.untrusted import untrusted
from mtg_deck_advisor.llm.anthropic import strict_schema
from mtg_deck_advisor.llm.ollama import Embedder
from mtg_deck_advisor.llm.types import ToolCall
from mtg_deck_advisor.retrieval.rerank import Reranker

log = structlog.get_logger(__name__)

INSTRUCTIONS = f"""\
Build Magic: The Gathering Commander decks from a user's card pool, using only \
the cards they own. Start with list_pools, then new_deck to draft a new deck or \
list_decks to continue one. Research with search_pool, get_card and search_rules, \
check with analyze_deck, then propose: propose_deck for a deck with nothing saved, \
propose_changes for a saved one. Code checks every proposal against the Commander \
rules and returns every problem to fix. When the user wants to keep a proposal, \
call approve_proposal with the confirming text show_proposal gives: the user is \
asked to allow it, and you can't allow it for them. Then apply_proposal saves it \
as the deck's next version, and approve_export (allowed the same way) and \
export_deck give the decklist.

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
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="A name for the deck, if the user gave one. Without one it's named for "
        "the pool, then for its commander once its first version is saved.",
    )


class RenameArgs(Arguments):
    deck_id: UUID = Field(description="The deck, from list_decks.")
    name: DeckName = Field(description="The name the user wants.")


class DeckArgs(Arguments):
    deck_id: UUID = Field(description="The deck, from list_decks or new_deck.")


class ProposalArgs(Arguments):
    proposal_id: UUID = Field(description="The proposal, from list_proposals or a propose tool.")


CONFIRMING = Field(
    min_length=1,
    max_length=1000,
    description="The server's summary of this decision, word for word, as show_proposal (or "
    "apply_proposal, for an export) gives it. The user sees it when asked to allow the call.",
)


class ApproveArgs(ProposalArgs):
    confirming: str = CONFIRMING


class RejectArgs(ProposalArgs):
    confirming: str = CONFIRMING
    reason: str = Field(min_length=1, max_length=500, description="Why it's rejected.")


class ExportArgs(DeckArgs):
    confirming: str = CONFIRMING


# The decisions only the user can make. Claude Code always asks the user to
# allow a tool marked this way, whatever their settings (ADR 0015).
DECISIONS = ("approve_proposal", "reject_proposal", "approve_export")
USER_INTERACTION = {"anthropic/requiresUserInteraction": True}
# Clients known to honour that marking; others can't make decisions.
DECISION_CLIENTS = frozenset({"claude-code"})


@dataclass
class McpSession:
    """One client's session: its connection and a tool context per deck it works on."""

    conn: psycopg.Connection
    embedder: Embedder
    reranker: Reranker[UUID] | None = None
    client_name: str = "client"
    decision_clients: frozenset[str] = DECISION_CLIENTS
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
            _tool(
                "rename_deck",
                "Rename a deck to the name the user asked for. Only its name changes.",
                RenameArgs,
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
        steps = [
            _tool("list_proposals", "List a deck's proposals with their status.", DeckArgs),
            _tool(
                "show_proposal",
                "Show a proposal: its status, problems, changes and resulting decklist.",
                ProposalArgs,
            ),
            _tool(
                "approve_proposal",
                "Approve a pending proposal, with the user's permission: they're asked to "
                "allow this call, and only then is their approval recorded.",
                ApproveArgs,
                meta=USER_INTERACTION,
            ),
            _tool(
                "reject_proposal",
                "Reject a pending proposal, with the reason, with the user's permission: "
                "confirming is show_proposal's text with 'Reject:' in place of 'Approve:'.",
                RejectArgs,
                meta=USER_INTERACTION,
            ),
            _tool(
                "apply_proposal",
                "Save an approved proposal as the deck's next version. Refused without "
                "the user's approval.",
                ProposalArgs,
            ),
            _tool(
                "approve_export",
                "Approve exporting the deck's latest version, with the user's permission: "
                "they're asked to allow this call.",
                ExportArgs,
                meta=USER_INTERACTION,
            ),
            _tool(
                "export_deck",
                "The deck's latest version as a plain decklist (1 Card Name lines). "
                "Refused unless the user approved exporting that version.",
                DeckArgs,
            ),
        ]
        return own + agent + steps

    def call(
        self, name: str, arguments: dict[str, Any], *, client: str | None = None
    ) -> types.CallToolResult:
        """Run one tool call and commit what it did. `client` is the name the client gave."""
        try:
            if name == "list_pools":
                return self._list_pools(ListPoolsArgs.model_validate(arguments))
            if name == "list_decks":
                return self._list_decks(ListDecksArgs.model_validate(arguments))
            if name == "new_deck":
                return self._new_deck(NewDeckArgs.model_validate(arguments))
            if name == "rename_deck":
                return self._rename(RenameArgs.model_validate(arguments), client)
            if name == "list_proposals":
                return self._list_proposals(DeckArgs.model_validate(arguments))
            if name == "show_proposal":
                return self._show_proposal(ProposalArgs.model_validate(arguments))
            if name == "apply_proposal":
                return self._apply(ProposalArgs.model_validate(arguments))
            if name == "export_deck":
                return self._export(DeckArgs.model_validate(arguments))
            if name in DECISIONS:
                return self._decide(name, arguments, client)
            return self._agent_tool(name, dict(arguments))
        except ValidationError as exc:
            return _result(f"Invalid arguments for {name}: {_describe(exc)}.", error=True)
        finally:
            self.conn.commit()

    def _decide(
        self, name: str, arguments: dict[str, Any], client: str | None
    ) -> types.CallToolResult:
        """Record a decision the user allowed in their client, if it can be trusted to ask."""
        log.info("mcp_decision", tool=name, client=client)
        if client not in self.decision_clients:
            return _result(
                f"Decisions can't be made from this client ({client or 'unnamed'}): it isn't "
                "known to ask the user before running them. The user can decide with the "
                "mtg-advisor CLI (for example `mtg-advisor approve PROPOSAL`) or the API.",
                error=True,
            )
        via = {"channel": "mcp", "client": client}
        try:
            if name == "approve_export":
                export = ExportArgs.model_validate(arguments)
                deck = load_deck(self.conn, export.deck_id)
                if deck is None:
                    return _result(
                        f"There is no deck {export.deck_id}. Use list_decks.", error=True
                    )
                summary = self._export_summary(export.deck_id)
                if summary is None:
                    return _result(
                        f"Deck {export.deck_id} has nothing saved yet: apply a proposal first.",
                        error=True,
                    )
                if mismatch := _mismatch(export.confirming, summary):
                    return mismatch
                approve_export(self.conn, export.deck_id, version=deck.version, via=via)
                return _result(
                    f"The user approved exporting version {deck.version}. "
                    "Get the decklist with export_deck."
                )
            args = (RejectArgs if name == "reject_proposal" else ApproveArgs).model_validate(
                arguments
            )
            proposal = load_proposal(self.conn, args.proposal_id)
            if proposal is None:
                return _result(f"There is no proposal {args.proposal_id}.", error=True)
            verb = "Reject" if isinstance(args, RejectArgs) else "Approve"
            if mismatch := _mismatch(args.confirming, self._proposal_summary(proposal, verb)):
                return mismatch
            if isinstance(args, RejectArgs):
                reject_proposal(self.conn, args.proposal_id, reason=args.reason, via=via)
                return _result(f"The user rejected proposal {args.proposal_id}.")
            approve_proposal(self.conn, args.proposal_id, via=via)
            return _result(
                f"The user approved proposal {args.proposal_id}. Save it with apply_proposal."
            )
        except ApprovalError as exc:
            return _result(str(exc), error=True)

    def _proposal_summary(self, proposal: ProposalRecord, verb: str) -> str:
        """The server's one-line account of deciding a proposal, shown in the user's prompt."""
        deck = load_deck(self.conn, proposal.deck_id)
        deck_name = deck.name if deck else "?"
        if proposal.kind == "deck":
            state = DeckState.from_dict(proposal.payload["deck"])
            commander = card_names(self.conn, [state.commander])[state.commander]
            what = f"{commander} as commander, {state.total} cards"
        else:
            add, remove = named_changes(self.conn, proposal)
            what = ", ".join(
                [f"remove {n} {name}" for name, n in remove]
                + [f"add {n} {name}" for name, n in add]
            )
        return f"{verb}: {what}, for deck {deck_name!r}"

    def _export_summary(self, deck_id: UUID) -> str | None:
        """The server's one-line account of exporting a deck's latest version."""
        deck = load_deck(self.conn, deck_id)
        if deck is None or deck.state is None or deck.version is None:
            return None
        commander = card_names(self.conn, [deck.state.commander])[deck.state.commander]
        return (
            f"Export: version {deck.version} of deck {deck.name!r}, {commander} as commander, "
            f"{deck.state.total} cards"
        )

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
        deck = load_deck(self.conn, deck_id)
        name = deck.name if deck else args.name
        return _result(f"Created deck {name!r}. Its deck_id is {deck_id}.")

    def _rename(self, args: RenameArgs, client: str | None) -> types.CallToolResult:
        previous = rename_deck(self.conn, args.deck_id, args.name)
        if previous is None:
            return _result(f"There is no deck {args.deck_id}. Use list_decks.", error=True)
        record_audit(
            self.conn,
            "agent",
            "rename",
            f"deck:{args.deck_id}",
            {"from": previous, "to": args.name, "client": client or self.client_name},
        )
        return _result(f"Renamed: {previous!r} is now {args.name!r}.")

    def _list_proposals(self, args: DeckArgs) -> types.CallToolResult:
        if load_deck(self.conn, args.deck_id) is None:
            return _result(f"There is no deck {args.deck_id}. Use list_decks.", error=True)
        proposals = deck_proposals(self.conn, args.deck_id)
        if not proposals:
            return _result("This deck has no proposals yet.")
        lines = [
            f"{p.id}  {'whole deck' if p.kind == 'deck' else 'changes'}, {p.status}, "
            f"{p.created_at:%Y-%m-%d %H:%M}"
            for p in proposals
        ]
        return _result(f"{len(proposals)} proposals, oldest first:\n" + "\n".join(lines))

    def _show_proposal(self, args: ProposalArgs) -> types.CallToolResult:
        proposal = load_proposal(self.conn, args.proposal_id)
        if proposal is None:
            return _result(f"There is no proposal {args.proposal_id}.", error=True)
        lines = [f"Rationale: {proposal.rationale}"]
        if proposal.citations:
            lines.append(f"Citations: {'; '.join(proposal.citations)}")
        lines += [f"Problem ({p['rule']}): {p['message']}" for p in proposal.problems]
        add, remove = named_changes(self.conn, proposal)
        lines += [f"- {count} {name}" for name, count in remove]
        lines += [f"+ {count} {name}" for name, count in add]
        deck = proposal.payload.get("deck")
        if deck:
            lines += ["", decklist(self.conn, DeckState.from_dict(deck))]
        kind = "whole-deck" if proposal.kind == "deck" else "changes"
        text = f"{kind} proposal {proposal.id} for deck {proposal.deck_id}: {proposal.status}\n"
        text += untrusted("\n".join(lines))
        if proposal.status == "pending":
            summary = self._proposal_summary(proposal, "Approve")
            text += (
                f'\nTo approve it, call approve_proposal with confirming: "{summary}"\n'
                "To reject it, use the same text with 'Reject:' in place of 'Approve:'."
            )
        return _result(text)

    def _apply(self, args: ProposalArgs) -> types.CallToolResult:
        proposal = load_proposal(self.conn, args.proposal_id)
        if proposal is None:
            return _result(f"There is no proposal {args.proposal_id}.", error=True)
        try:
            version = apply_proposal(self.conn, args.proposal_id)
        except ApprovalError as exc:
            return _result(str(exc), error=True)
        summary = self._export_summary(proposal.deck_id)
        return _result(
            f"Saved as version {version} of deck {proposal.deck_id}.\n"
            f'To export it, call approve_export with confirming: "{summary}"'
        )

    def _export(self, args: DeckArgs) -> types.CallToolResult:
        deck = load_deck(self.conn, args.deck_id)
        if deck is None or deck.version is None:
            return _result(f"Deck {args.deck_id} has nothing saved to export.", error=True)
        try:
            text = export_deck(self.conn, args.deck_id, version=deck.version)
        except ApprovalError as exc:
            return _result(str(exc), error=True)
        return _result(f"Version {deck.version}, ready to import:\n{untrusted(text)}")

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
        made = len(ctx.proposals)
        result = execute(ctx, ToolCall(id=f"mcp-{turn}", name=name, arguments=arguments), turn=turn)
        record_progress(self.conn, ctx.run_id, turns=turn, cost_usd=0.0)
        content = result.content
        if len(ctx.proposals) > made:
            # The agent's results say "proposal N" so recorded runs replay exactly;
            # an MCP client needs the ID itself, to show, approve or apply it.
            content += f"\nIts proposal_id is {ctx.proposals[-1]}."
        return _result(content, error=result.is_error)

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
        info = ctx.session.client_params.client_info if ctx.session.client_params else None
        return session.call(params.name, params.arguments or {}, client=info.name if info else None)

    return Server(
        "mtg-deck-advisor",
        version="0.1.0",
        instructions=INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


def _mismatch(given: str, summary: str) -> types.CallToolResult | None:
    """An error if `confirming` isn't the server's summary, word for word."""
    if " ".join(given.split()) == " ".join(summary.split()):
        return None
    return _result(
        "confirming must be the server's summary of this decision, word for word, because the "
        f'user sees it when asked to allow the call: "{summary}"',
        error=True,
    )


def _tool(
    name: str, description: str, args: type[BaseModel], meta: dict[str, Any] | None = None
) -> types.Tool:
    return types.Tool(
        name=name,
        description=description,
        input_schema=strict_schema(args.model_json_schema()),
        meta=meta,
    )


def _result(text: str, *, error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=error)


def _describe(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in e['loc']) or 'arguments'}: {e['msg']}"
        for e in error.errors()
    )
