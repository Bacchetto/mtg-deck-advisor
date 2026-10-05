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
model's, so calling a decision tool doesn't make one: the server asks the
user directly with an MCP elicitation form, written from what the database
holds, and only the user's Accept records it (`actor = user`). A declined
form changes nothing; a client that can't show forms is sent to the CLI.
Apply and export still refuse without the recorded approval (GRD-2, GRD-5).
See ADR 0015.

A deck with a saved version is refined (`propose_changes`); one without is
drafted (`propose_deck`). Which one is decided when the session first touches
the deck, as for the agent's runs.
"""

import hashlib
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
from mtg_deck_advisor.deck.state import DeckState
from mtg_deck_advisor.deck.store import (
    Pool,
    card_names,
    create_deck,
    decklist,
    list_decks,
    list_pools,
    load_deck,
    load_pool,
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
from mtg_deck_advisor.guardrails.proposals import deck_proposals, load_proposal, named_changes
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
rules and returns every problem to fix. When the user wants to keep a proposal, \
call approve_proposal: the user is asked to confirm in a form, and you can't \
confirm for them. Then apply_proposal saves it as the deck's next version, and \
approve_export (confirmed the same way) and export_deck give the decklist.

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


class DeckArgs(Arguments):
    deck_id: UUID = Field(description="The deck, from list_decks or new_deck.")


class ProposalArgs(Arguments):
    proposal_id: UUID = Field(description="The proposal, from list_proposals or a propose tool.")


class RejectArgs(ProposalArgs):
    reason: str = Field(min_length=1, max_length=500, description="Why it's rejected.")


@dataclass(frozen=True)
class Decision:
    """A decision the user is about to be asked to confirm."""

    tool: str
    subject: str
    # What the user is shown: the server's account, from the database.
    message: str
    proposal_id: UUID | None = None
    deck_id: UUID | None = None
    version: int | None = None
    reason: str | None = None


# The decisions only the user can make, and the verb for each.
DECISIONS = {
    "approve_proposal": "approval",
    "reject_proposal": "rejection",
    "approve_export": "export_approval",
}
# The form: Accept or Decline, with an optional note recorded with the decision.
DECISION_FORM: dict[str, Any] = {
    "type": "object",
    "properties": {
        "note": {"type": "string", "title": "Note (optional)", "maxLength": 500},
    },
}


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
        steps = [
            _tool("list_proposals", "List a deck's proposals with their status.", DeckArgs),
            _tool(
                "show_proposal",
                "Show a proposal: its status, problems, changes and resulting decklist.",
                ProposalArgs,
            ),
            _tool(
                "approve_proposal",
                "Ask the user to approve a pending proposal. The user confirms or declines "
                "in a form; only their confirmation records the approval.",
                ProposalArgs,
            ),
            _tool(
                "reject_proposal",
                "Ask the user to reject a pending proposal, with the reason. The user "
                "confirms or declines in a form.",
                RejectArgs,
            ),
            _tool(
                "apply_proposal",
                "Save an approved proposal as the deck's next version. Refused without "
                "the user's approval.",
                ProposalArgs,
            ),
            _tool(
                "approve_export",
                "Ask the user to approve exporting the deck's latest version. The user "
                "confirms or declines in a form.",
                DeckArgs,
            ),
            _tool(
                "export_deck",
                "The deck's latest version as a plain decklist (1 Card Name lines). "
                "Refused unless the user approved exporting that version.",
                DeckArgs,
            ),
        ]
        return own + agent + steps

    def call(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        """Run one tool call and commit what it did."""
        try:
            if name == "list_pools":
                return self._list_pools(ListPoolsArgs.model_validate(arguments))
            if name == "list_decks":
                return self._list_decks(ListDecksArgs.model_validate(arguments))
            if name == "new_deck":
                return self._new_deck(NewDeckArgs.model_validate(arguments))
            if name == "list_proposals":
                return self._list_proposals(DeckArgs.model_validate(arguments))
            if name == "show_proposal":
                return self._show_proposal(ProposalArgs.model_validate(arguments))
            if name == "apply_proposal":
                return self._apply(ProposalArgs.model_validate(arguments))
            if name == "export_deck":
                return self._export(DeckArgs.model_validate(arguments))
            if name in DECISIONS:
                return _result(f"{name} needs the user's confirmation.", error=True)
            return self._agent_tool(name, dict(arguments))
        except ValidationError as exc:
            return _result(f"Invalid arguments for {name}: {_describe(exc)}.", error=True)
        finally:
            self.conn.commit()

    def prepare_decision(
        self, name: str, arguments: dict[str, Any]
    ) -> Decision | types.CallToolResult:
        """What the user will be asked to confirm, or why there's nothing to ask."""
        try:
            if name == "approve_export":
                deck_args = DeckArgs.model_validate(arguments)
                return self._export_decision(deck_args.deck_id)
            args = (RejectArgs if name == "reject_proposal" else ProposalArgs).model_validate(
                arguments
            )
            reason = args.reason if isinstance(args, RejectArgs) else None
            return self._proposal_decision(name, args.proposal_id, reason)
        except ValidationError as exc:
            return _result(f"Invalid arguments for {name}: {_describe(exc)}.", error=True)
        finally:
            self.conn.rollback()  # asking is read-only

    def decide(
        self, decision: Decision, *, accepted: bool, note: str | None
    ) -> types.CallToolResult:
        """Record the user's answer to a decision form."""
        try:
            if not accepted:
                record_audit(
                    self.conn,
                    "user",
                    f"{DECISIONS[decision.tool]}_declined",
                    decision.subject,
                    {"client": self.client_name},
                )
                return _result("The user declined, so nothing changed.")
            if decision.tool == "approve_proposal":
                assert decision.proposal_id is not None  # noqa: S101 (set for this tool)
                approve_proposal(self.conn, decision.proposal_id, note=note)
                return _result(
                    f"The user approved proposal {decision.proposal_id}. "
                    "Save it with apply_proposal."
                )
            if decision.tool == "reject_proposal":
                assert decision.proposal_id is not None  # noqa: S101 (set for this tool)
                reject_proposal(
                    self.conn, decision.proposal_id, reason=note or decision.reason or ""
                )
                return _result(f"The user rejected proposal {decision.proposal_id}.")
            assert decision.deck_id is not None  # noqa: S101 (set for this tool)
            approve_export(self.conn, decision.deck_id, version=decision.version, note=note)
            return _result(
                f"The user approved exporting version {decision.version}. "
                "Get the decklist with export_deck."
            )
        except ApprovalError as exc:
            return _result(str(exc), error=True)
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
        return _result(
            f"{kind} proposal {proposal.id} for deck {proposal.deck_id}: {proposal.status}\n"
            + untrusted("\n".join(lines))
        )

    def _apply(self, args: ProposalArgs) -> types.CallToolResult:
        proposal = load_proposal(self.conn, args.proposal_id)
        if proposal is None:
            return _result(f"There is no proposal {args.proposal_id}.", error=True)
        try:
            version = apply_proposal(self.conn, args.proposal_id)
        except ApprovalError as exc:
            return _result(str(exc), error=True)
        return _result(f"Saved as version {version} of deck {proposal.deck_id}.")

    def _export(self, args: DeckArgs) -> types.CallToolResult:
        deck = load_deck(self.conn, args.deck_id)
        if deck is None or deck.version is None:
            return _result(f"Deck {args.deck_id} has nothing saved to export.", error=True)
        try:
            text = export_deck(self.conn, args.deck_id, version=deck.version)
        except ApprovalError as exc:
            return _result(str(exc), error=True)
        return _result(f"Version {deck.version}, ready to import:\n{untrusted(text)}")

    def _proposal_decision(
        self, name: str, proposal_id: UUID, reason: str | None
    ) -> Decision | types.CallToolResult:
        proposal = load_proposal(self.conn, proposal_id)
        if proposal is None:
            return _result(f"There is no proposal {proposal_id}.", error=True)
        if proposal.status != "pending":
            return _result(
                f"Proposal {proposal_id} is {proposal.status}; only a pending proposal can be "
                "decided.",
                error=True,
            )
        deck = load_deck(self.conn, proposal.deck_id)
        deck_name = deck.name if deck else "?"
        if proposal.kind == "deck":
            state = DeckState.from_dict(proposal.payload["deck"])
            commander = card_names(self.conn, [state.commander])[state.commander]
            what = f"a whole deck: {commander} as commander, {state.total} cards"
        else:
            add, remove = named_changes(self.conn, proposal)
            what = "changes: " + ", ".join(
                [f"remove {n} {name}" for name, n in remove]
                + [f"add {n} {name}" for name, n in add]
            )
        verb = "Approve" if name == "approve_proposal" else "Reject"
        message = f"{verb} this proposal for your deck {deck_name!r}?\n{what}"
        if reason:
            message += f"\nReason given: {reason}"
        return Decision(
            name, f"proposal:{proposal_id}", message, proposal_id=proposal_id, reason=reason
        )

    def _export_decision(self, deck_id: UUID) -> Decision | types.CallToolResult:
        deck = load_deck(self.conn, deck_id)
        if deck is None:
            return _result(f"There is no deck {deck_id}. Use list_decks.", error=True)
        if deck.state is None or deck.version is None:
            return _result(
                f"Deck {deck_id} has nothing saved yet: apply a proposal first.", error=True
            )
        commander = card_names(self.conn, [deck.state.commander])[deck.state.commander]
        message = (
            f"Approve exporting version {deck.version} of your deck {deck.name!r}?\n"
            f"{commander} as commander, {deck.state.total} cards"
        )
        return Decision(
            "approve_export", f"deck:{deck_id}", message, deck_id=deck_id, version=deck.version
        )

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
    ) -> types.CallToolResult | types.InputRequiredResult:
        if params.name not in DECISIONS:
            return session.call(params.name, params.arguments or {})
        return await _decide(ctx, params)

    async def _decide(
        ctx: ServerRequestContext[Any], params: types.CallToolRequestParams
    ) -> types.CallToolResult | types.InputRequiredResult:
        """Ask the user, not the model: only the user's answer to the form decides.

        Two protocol eras ask differently. Where the server can send the client a
        request mid-call, it sends the form and waits. Otherwise (the 2026-07-28
        revision) it returns "input required" with the form, and the client calls
        again with the user's answer. The state handed back is a fingerprint of
        the exact message shown, so an answer is recorded only for what the user
        saw: if the proposal changed in between, the user is asked again.
        """
        name, arguments = params.name, params.arguments or {}
        decision = session.prepare_decision(name, arguments)
        if isinstance(decision, types.CallToolResult):
            return decision
        if not ctx.session.check_client_capability(ELICITATION):
            return _result(
                "This client can't ask the user to confirm, so it can't record a decision. "
                "The user can do it with the mtg-advisor CLI (for example "
                "`mtg-advisor approve PROPOSAL`) or the API.",
                error=True,
            )
        if ctx.session.can_send_request:
            answer = await ctx.session.elicit_form(
                decision.message, DECISION_FORM, related_request_id=ctx.request_id
            )
        else:
            shown = _fingerprint(decision.message)
            given = (params.input_responses or {}).get(CONFIRM)
            if not isinstance(given, types.ElicitResult) or params.request_state != shown:
                form = types.ElicitRequestFormParams(
                    mode="form", message=decision.message, requested_schema=DECISION_FORM
                )
                return types.InputRequiredResult(
                    input_requests={CONFIRM: types.ElicitRequest(params=form)},
                    request_state=shown,
                )
            answer = given
        note = (answer.content or {}).get("note") if answer.action == "accept" else None
        return session.decide(
            decision, accepted=answer.action == "accept", note=str(note) if note else None
        )

    return Server(
        "mtg-deck-advisor",
        version="0.1.0",
        instructions=INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


ELICITATION = types.ClientCapabilities(elicitation=types.ElicitationCapability())
# The key of the confirmation form in an "input required" round trip.
CONFIRM = "confirm"


def _fingerprint(message: str) -> str:
    return hashlib.sha256(message.encode()).hexdigest()


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
