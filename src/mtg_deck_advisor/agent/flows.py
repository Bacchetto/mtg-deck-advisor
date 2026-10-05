"""What the agent is used for: drafting a deck, refining it, and answering rules questions.

Each flow starts a recorded run, gives the agent its task in the first user
turn, and runs the bounded loop. The flows end at proposals: approving,
applying and exporting are separate steps the user takes (guardrails.approvals).

**Rules answers are checked by code (RAG-3, RAG-4).** The agent must end its
answer with the rules it relies on. The answer is returned only if it cites at
least one rule and every rule it cites was retrieved in the run; anything
else, including an honest "NOT FOUND", comes back as not found, with the
reason. An answer from memory, or with an invented rule number, never reaches
the user.
"""

import re
from dataclasses import dataclass
from uuid import UUID

import psycopg

from mtg_deck_advisor.agent.loop import DEFAULT_MAX_TOKENS, RunResult, run_agent
from mtg_deck_advisor.agent.prompts import system_prompt
from mtg_deck_advisor.agent.runs import Task, start_run
from mtg_deck_advisor.agent.tools import ToolContext
from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.deck.facts import load_card_facts
from mtg_deck_advisor.deck.store import (
    Pool,
    PoolSource,
    create_deck,
    create_pool,
    decklist,
    load_deck,
    load_pool,
)
from mtg_deck_advisor.guardrails.untrusted import untrusted
from mtg_deck_advisor.ingestion.pool import parse_csv, parse_text
from mtg_deck_advisor.ingestion.resolve import resolve
from mtg_deck_advisor.llm.client import ModelClient
from mtg_deck_advisor.llm.factory import build_embedder, build_provider
from mtg_deck_advisor.llm.ollama import Embedder
from mtg_deck_advisor.llm.recording import DatabaseRecorder
from mtg_deck_advisor.retrieval.rerank import Reranker, build_reranker

CITATIONS_LINE = re.compile(r"^\s*citations?\s*:(.*)$", re.IGNORECASE | re.MULTILINE)
RULE_NUMBERS = re.compile(r"\b\d{3}(?:\.\d+[a-z]*)?\b")


@dataclass(frozen=True)
class AgentServices:
    """What a run needs: a connection, the model client (its cost cap is the
    run's budget), retrieval, and the turn limit."""

    conn: psycopg.Connection
    client: ModelClient
    embedder: Embedder
    reranker: Reranker[UUID] | None = None
    max_turns: int = 30
    max_tokens: int = DEFAULT_MAX_TOKENS


def build_services(conn: psycopg.Connection, settings: Settings) -> AgentServices:
    """Services from settings: the agent model with its cost cap, every call recorded."""
    client = ModelClient(
        build_provider(settings),
        settings.agent_model,
        recorder=DatabaseRecorder(settings),
        cost_cap_usd=settings.agent_cost_cap_usd,
    )
    return AgentServices(
        conn=conn,
        client=client,
        embedder=build_embedder(settings),
        reranker=build_reranker(settings),
        max_turns=settings.agent_max_turns,
    )


# --- pools ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PoolImport:
    pool_id: UUID
    # Names that matched no card, or more than one: left out of the pool.
    unresolved: list[str]
    # Lines that couldn't be read at all.
    problems: list[str]


def import_pool(
    conn: psycopg.Connection, text: str, *, name: str, source: PoolSource = "text"
) -> PoolImport:
    """Parse a pasted list or CSV export, resolve its names, and save what matched."""
    parsed = parse_csv(text) if source == "csv" else parse_text(text)
    resolved = resolve(conn, parsed.entries)
    counts: dict[UUID, int] = {}
    for match in resolved.matched:
        counts[match.card.oracle_id] = counts.get(match.card.oracle_id, 0) + match.entry.quantity
    unresolved = [item.entry.name for item in resolved.unknown] + [
        item.entry.name for item in resolved.ambiguous
    ]
    pool_id = create_pool(conn, counts, name=name, source=source)
    return PoolImport(
        pool_id=pool_id,
        unresolved=list(dict.fromkeys(unresolved)),
        problems=[problem.message for problem in parsed.problems],
    )


# --- draft and refine ------------------------------------------------------------------


class NotFoundError(LookupError):
    """There is no pool or deck with that ID."""


class NotReadyError(ValueError):
    """The deck isn't in a state the flow can start from."""


@dataclass(frozen=True)
class PreparedRun:
    """A run that has been recorded as started, with the prompt it will be given.

    Preparing is quick and executing is slow, so an interface can prepare a
    run, answer at once, and execute it in the background on another connection.
    """

    run_id: UUID
    task: Task
    pool_id: UUID | None
    deck_id: UUID | None
    prompt: str


@dataclass(frozen=True)
class DraftResult:
    deck_id: UUID
    run: RunResult


def prepare_draft(
    conn: psycopg.Connection, model: str, pool_id: UUID, *, request: str = "", name: str = "Draft"
) -> PreparedRun:
    """A new deck for the pool, and a started run that will propose its first version."""
    pool = _pool(conn, pool_id)
    deck_id = create_deck(conn, pool_id, name=name)
    run_id = start_run(conn, "draft", model, pool_id=pool_id, deck_id=deck_id)
    return PreparedRun(run_id, "draft", pool_id, deck_id, _draft_prompt(conn, pool, request))


def prepare_refine(
    conn: psycopg.Connection, model: str, deck_id: UUID, *, request: str
) -> PreparedRun:
    """A started run that will propose changes to the deck's latest saved version."""
    deck = load_deck(conn, deck_id)
    if deck is None:
        raise NotFoundError(f"there is no deck {deck_id}")
    if deck.state is None:
        raise NotReadyError(f"deck {deck_id} has no saved version to refine")
    run_id = start_run(conn, "refine", model, pool_id=deck.pool_id, deck_id=deck_id)
    prompt = (
        f"Refine my saved deck (version {deck.version}): {request}\n\n"
        f"The deck, commander first:\n{untrusted(decklist(conn, deck.state))}"
    )
    return PreparedRun(run_id, "refine", deck.pool_id, deck_id, prompt)


def execute_run(services: AgentServices, prepared: PreparedRun) -> RunResult:
    """Run the agent for a prepared run, on the services' connection."""
    pool = _pool(services.conn, prepared.pool_id) if prepared.pool_id else None
    ctx = ToolContext(
        conn=services.conn,
        embedder=services.embedder,
        reranker=services.reranker,
        run_id=prepared.run_id,
        task=prepared.task,
        pool=pool,
        deck_id=prepared.deck_id,
    )
    return _run(services, ctx, prepared.prompt)


def draft_deck(
    services: AgentServices, pool_id: UUID, *, request: str = "", name: str = "Draft"
) -> DraftResult:
    """A new deck for the pool, and a run that proposes its first version."""
    model = services.client.model
    prepared = prepare_draft(services.conn, model, pool_id, request=request, name=name)
    assert prepared.deck_id is not None  # noqa: S101 (a draft always has its deck)
    return DraftResult(deck_id=prepared.deck_id, run=execute_run(services, prepared))


def refine_deck(services: AgentServices, deck_id: UUID, *, request: str) -> RunResult:
    """A run that proposes changes to the deck's latest saved version."""
    prepared = prepare_refine(services.conn, services.client.model, deck_id, request=request)
    return execute_run(services, prepared)


def commander_candidates(conn: psycopg.Connection, pool: Pool) -> list[tuple[str, str]]:
    """The pool's cards that can lead a deck, by name, with their color identity."""
    facts = load_card_facts(conn, pool.cards)
    return [
        (card.name, "".join(c for c in "WUBRG" if c in card.color_identity) or "colorless")
        for card in sorted(facts.values(), key=lambda card: card.name)
        if card.commander_eligibility.eligible and card.commander_legality == "legal"
    ]


def _draft_prompt(conn: psycopg.Connection, pool: Pool, request: str) -> str:
    listing = "\n".join(
        f"{name} ({identity})" for name, identity in commander_candidates(conn, pool)
    )
    return (
        "Draft a Commander deck from my card pool.\n"
        f"{request.strip() or 'Choose the commander my pool supports best.'}\n\n"
        f"My pool has {sum(pool.cards.values())} cards ({len(pool.cards)} different). "
        "These can be the commander:\n"
        f"{untrusted(listing or 'None: the pool has no card that can be a commander.')}"
    )


# --- rules answers ----------------------------------------------------------------------


@dataclass(frozen=True)
class CitedRule:
    number: str
    text: str


@dataclass(frozen=True)
class RulesAnswer:
    question: str
    found: bool
    # The answer, without its citations line; None unless found.
    answer: str | None
    citations: list[CitedRule]
    # Why there's no answer; empty when found.
    reason: str
    run: RunResult


def answer_rules_question(services: AgentServices, question: str) -> RulesAnswer:
    ctx = _context(services, "rules", None, None)
    run = _run(services, ctx, question)

    def not_found(reason: str) -> RulesAnswer:
        return RulesAnswer(question, False, None, [], reason, run)

    if run.status != "completed" or run.final_text is None:
        return not_found(f"The run ended with status {run.status} before answering.")
    text = run.final_text.strip()
    if text.upper().startswith("NOT FOUND"):
        return not_found(text)
    lines = CITATIONS_LINE.findall(text)
    cited = list(dict.fromkeys(RULE_NUMBERS.findall(lines[-1]))) if lines else []
    if not cited:
        return not_found("The answer didn't cite any rules, so it was withheld.")
    if unseen := [number for number in cited if number not in ctx.seen_rules]:
        return not_found(
            f"The answer cited rules it never retrieved ({', '.join(unseen)}), so it was withheld."
        )
    rows: dict[str, str] = dict(
        services.conn.execute(
            "SELECT number, text FROM rules WHERE number = ANY(%s)", (cited,)
        ).fetchall()
    )
    body = CITATIONS_LINE.sub("", text).strip()
    citations = [CitedRule(number, rows[number]) for number in cited if number in rows]
    return RulesAnswer(question, True, body, citations, "", run)


# --- shared ---------------------------------------------------------------------------


def _pool(conn: psycopg.Connection, pool_id: UUID) -> Pool:
    pool = load_pool(conn, pool_id)
    if pool is None:
        raise NotFoundError(f"there is no pool {pool_id}")
    return pool


def _context(
    services: AgentServices, task: Task, pool: Pool | None, deck_id: UUID | None
) -> ToolContext:
    run_id = start_run(
        services.conn,
        task,
        services.client.model,
        pool_id=pool.id if pool else None,
        deck_id=deck_id,
    )
    return ToolContext(
        conn=services.conn,
        embedder=services.embedder,
        reranker=services.reranker,
        run_id=run_id,
        task=task,
        pool=pool,
        deck_id=deck_id,
    )


def _run(services: AgentServices, ctx: ToolContext, prompt: str) -> RunResult:
    return run_agent(
        services.client,
        ctx,
        system=system_prompt(ctx.task),
        prompt=prompt,
        max_turns=services.max_turns,
        max_tokens=services.max_tokens,
    )
