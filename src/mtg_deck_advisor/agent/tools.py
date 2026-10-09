"""The agent's tools (AGT-1, AGT-4, GRD-1, GRD-3, OBS-1). See ADR 0013.

Each tool has a Pydantic argument model: its JSON schema is what the model
sees, and the same model validates what the model sends back. `execute` runs
one call and always returns a result for the model, never an exception:

- **An unknown tool, bad arguments, or a tool the task doesn't offer** is an
  error result saying what was wrong, so the model can fix the call within
  the same run (AGT-4).
- **A failure inside a tool** is an error result too, without internals; the
  details go to the log. Each call runs in its own savepoint, so a failed
  query never poisons the connection for the rest of the run.
- **A rejected proposal** is an error result listing every problem.

Nothing a tool does can change a deck. The only writes are proposals, which
wait for the user (GRD-1, GRD-2); approving, applying and exporting have no
tool at all.

Data from outside the project (card names, oracle text, rule text) reaches
the model only inside `<untrusted>` delimiters (GRD-3). Every call is recorded
with its arguments, result, outcome and latency (OBS-1).
"""

import difflib
import re
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Literal, Self
from uuid import UUID

import psycopg
import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from mtg_deck_advisor.agent.runs import Task as Task
from mtg_deck_advisor.agent.runs import ToolOutcome, record_tool_call
from mtg_deck_advisor.deck.facts import FREE_BASICS, load_card_facts
from mtg_deck_advisor.deck.goals import Goal, check_goals
from mtg_deck_advisor.deck.state import DeckState
from mtg_deck_advisor.deck.store import Pool, card_names, load_deck
from mtg_deck_advisor.guardrails.commander import validate
from mtg_deck_advisor.guardrails.proposals import save_proposal
from mtg_deck_advisor.guardrails.untrusted import untrusted
from mtg_deck_advisor.ingestion.cards import fold_name, loose_name
from mtg_deck_advisor.llm.ollama import Embedder
from mtg_deck_advisor.llm.roles import Role, load_roles
from mtg_deck_advisor.llm.types import ToolCall, ToolResult, ToolSpec
from mtg_deck_advisor.retrieval.rerank import Reranker
from mtg_deck_advisor.retrieval.search import CardFilters, SearchMode, search_cards, search_rules

log = structlog.get_logger(__name__)

# A Comprehensive Rules number: "903", "903.5" or "903.5c".
RULE_NUMBER = re.compile(r"\d{3}(\.\d+[a-z]*)?")

TASK_WORDS: dict[Task, str] = {
    "draft": "drafting a deck",
    "refine": "refining a deck",
    "rules": "answering a rules question",
}


class ToolError(Exception):
    """A problem the model can fix: the message goes back to it as an error result."""


@dataclass(frozen=True)
class Output:
    content: str
    # "rejected": a proposal that failed its checks.
    outcome: Literal["ok", "rejected"] = "ok"


@dataclass(frozen=True)
class PoolCard:
    oracle_id: UUID
    name: str
    mana_cost: str
    mana_value: float
    type_line: str
    oracle_text: str
    color_identity: str
    # Copies owned; 0 for a free basic land, which any deck may use.
    owned: int


@dataclass
class ToolContext:
    """One run's tools' view of the world, and what the run has seen and proposed."""

    conn: psycopg.Connection
    embedder: Embedder
    run_id: UUID
    task: Task
    pool: Pool | None
    deck_id: UUID | None
    reranker: Reranker[UUID] | None = None
    # How the search tools search; the defaults are what ships (evals vary them).
    card_search_mode: SearchMode = "hybrid"
    rules_search_mode: SearchMode = "vector"
    # The deck as the latest proposal in this run would leave it.
    draft: DeckState | None = None
    # Cards and rules this run's tool results have shown the model, so a
    # proposal's citations can be checked against them.
    seen_cards: set[UUID] = field(default_factory=set)
    seen_rules: set[str] = field(default_factory=set)
    proposals: list[UUID] = field(default_factory=list)
    _cards: dict[UUID, PoolCard] | None = field(default=None, repr=False)
    _names: dict[str, PoolCard] | None = field(default=None, repr=False)


# --- argument models -----------------------------------------------------------------


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SearchPoolArgs(Arguments):
    query: str = Field(
        min_length=1,
        max_length=300,
        description="What to look for, in plain words: 'cheap ramp', 'flying blockers', "
        "'ways to destroy artifacts', or a card name.",
    )
    types: list[str] = Field(
        default=[],
        description="Words that must all appear in the type line, such as ['Creature'] or "
        "['Legendary', 'Creature'].",
    )
    color_identity_within: str | None = Field(
        default=None,
        description="Only cards whose color identity is within these colors, as letters from "
        "WUBRG ('' for colorless only). Use the commander's identity.",
    )
    mana_value_max: float | None = Field(default=None, ge=0, description="Highest mana value.")
    k: int = Field(default=10, ge=1, le=40, description="How many cards to return.")


class GetCardArgs(Arguments):
    name: str = Field(min_length=1, max_length=200, description="The card's name.")


class SearchRulesArgs(Arguments):
    question: str = Field(
        min_length=1, max_length=500, description="The rules question, in plain words."
    )
    k: int = Field(default=5, ge=1, le=10, description="How many rules to return.")


class AnalyzeDeckArgs(Arguments):
    pass


class CardCount(Arguments):
    name: str = Field(min_length=1, max_length=200, description="The card's name.")
    count: int = Field(default=1, ge=1, le=99, description="How many copies.")


class ProposeDeckArgs(Arguments):
    commander: str = Field(min_length=1, max_length=200, description="The commander's name.")
    cards: list[CardCount] = Field(
        max_length=150,
        description="The other 99 cards, basic lands included, with their counts.",
    )
    rationale: str = Field(
        min_length=1,
        max_length=4000,
        description="Why this deck: its plan and how the cards serve it, for the user.",
    )
    citations: list[str] = Field(
        default=[],
        max_length=50,
        description="Rule numbers and card names, from this run's tool results, that the "
        "rationale relies on.",
    )


class FindByRoleArgs(Arguments):
    role: Role = Field(description="The role tag to look for.")
    mana_value_max: float | None = Field(default=None, ge=0, description="Highest mana value.")
    k: int = Field(default=20, ge=1, le=40, description="How many cards to return.")


class ProposeChangesArgs(Arguments):
    add: list[CardCount] = Field(default=[], max_length=50, description="Cards to add.")
    remove: list[CardCount] = Field(default=[], max_length=50, description="Cards to remove.")
    rationale: str = Field(
        min_length=1, max_length=4000, description="Why these changes, for the user."
    )
    citations: list[str] = Field(
        default=[],
        max_length=50,
        description="Rule numbers and card names, from this run's tool results, that the "
        "rationale relies on.",
    )
    goals: list[Goal] = Field(
        default=[],
        max_length=10,
        description="The request's goals that code can check against the saved deck, such as "
        "a land count or a role's count. A change that misses one comes back with it listed. "
        "Leave out a goal the pool can't meet, and tell the user why.",
    )

    @model_validator(mode="after")
    def _changes_something(self) -> Self:
        if not self.add and not self.remove:
            raise ValueError("propose at least one card to add or remove")
        return self


# --- the tools ---------------------------------------------------------------------


def _search_pool(ctx: ToolContext, args: SearchPoolArgs) -> Output:
    pool = _require_pool(ctx)
    try:
        filters = CardFilters(
            oracle_ids=list(pool.cards),
            types=args.types,
            color_identity_within=args.color_identity_within,
            mana_value_max=args.mana_value_max,
        )
    except ValidationError as exc:
        raise ToolError(f"Invalid search filters: {_describe(exc)}") from exc
    hits = search_cards(
        ctx.conn,
        ctx.embedder,
        args.query,
        filters,
        k=args.k,
        mode=ctx.card_search_mode,
        reranker=ctx.reranker,
    )
    if not hits:
        return Output("No cards in the user's pool match that search.")
    cards = _pool_cards(ctx)
    roles = load_roles(ctx.conn, [hit.oracle_id for hit in hits])
    lines = []
    for number, hit in enumerate(hits, 1):
        ctx.seen_cards.add(hit.oracle_id)
        card = cards[hit.oracle_id]
        tags = ", ".join(roles[hit.oracle_id].roles) if hit.oracle_id in roles else "untagged"
        lines.append(
            f"{number}. {card.name} | {card.mana_cost or 'no cost'} | {card.type_line} | "
            f"{_one_line(card.oracle_text)} | owned: {card.owned} | roles: {tags}"
        )
    return Output(f"{len(hits)} pool cards, best match first:\n{untrusted(chr(10).join(lines))}")


def _get_card(ctx: ToolContext, args: GetCardArgs) -> Output:
    card = _find(ctx, args.name)
    if card is None:
        raise ToolError(f"get_card found no match.\n{untrusted(_not_found(ctx, args.name))}")
    ctx.seen_cards.add(card.oracle_id)
    roles = load_roles(ctx.conn, [card.oracle_id]).get(card.oracle_id)
    owned = str(card.owned) if card.owned else "any number (a basic land)"
    details = "\n".join(
        [
            f"Name: {card.name}",
            f"Mana cost: {card.mana_cost or 'none'} (mana value {card.mana_value:g})",
            f"Type: {card.type_line}",
            f"Color identity: {card.color_identity or 'colorless'}",
            f"Text: {_one_line(card.oracle_text) or '(none)'}",
            f"Owned: {owned}",
            f"Roles: {', '.join(roles.roles) if roles and roles.roles else 'none tagged'}",
        ]
    )
    return Output(untrusted(details))


def _search_rules(ctx: ToolContext, args: SearchRulesArgs) -> Output:
    hits = search_rules(ctx.conn, ctx.embedder, args.question, k=args.k, mode=ctx.rules_search_mode)
    if not hits:
        return Output("No rules match that question.")
    ctx.seen_rules.update(hit.number for hit in hits)
    lines = [f"{hit.number} {_one_line(hit.text)}" for hit in hits]
    return Output(
        f"{len(hits)} Comprehensive Rules, best match first. Cite them by number:\n"
        f"{untrusted(chr(10).join(lines))}"
    )


@dataclass(frozen=True)
class _Profile:
    """A deck's numbers, as analyze_deck reports them."""

    lands: int
    # Nonland cards besides the commander, by mana value ("0"-"6", "7+").
    curve: Counter[str]
    roles: Counter[str]
    untagged: int
    average_mana_value: float


CURVE_BUCKETS = [str(n) for n in range(7)] + ["7+"]


def _profile(ctx: ToolContext, deck: DeckState) -> _Profile:
    rows = ctx.conn.execute(
        "SELECT oracle_id, cmc, type_line FROM cards WHERE oracle_id = ANY(%s)",
        (list(deck.cards),),
    ).fetchall()
    info = {row[0]: row[1:] for row in rows}
    roles = load_roles(ctx.conn, deck.cards)
    lands = untagged = 0
    curve: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()
    mana_values: list[float] = []
    for card, copies in deck.cards.items():
        mana_value, type_line = info.get(card, (0.0, ""))
        if "Land" in type_line.split("—")[0]:
            lands += copies
            continue
        curve["7+" if mana_value >= 7 else str(int(mana_value))] += copies
        mana_values += [float(mana_value)] * copies
        tags = roles[card].roles if card in roles else []
        for role in tags:
            role_counts[role] += copies
        if not tags:
            untagged += copies
    average = sum(mana_values) / len(mana_values) if mana_values else 0.0
    return _Profile(lands, curve, role_counts, untagged, average)


def _comparison(ctx: ToolContext, saved: DeckState, version: int, deck: DeckState) -> list[str]:
    """How a proposed deck differs from the saved version, so a refine can check its request."""
    before, after = _profile(ctx, saved), _profile(ctx, deck)

    def change(a: object, b: object) -> str:
        return f"{a} → {b}" if a != b else f"{a} (unchanged)"

    roles = sorted(set(before.roles) | set(after.roles), key=lambda r: -after.roles[r])
    changed_roles = [r for r in roles if before.roles[r] != after.roles[r]]
    buckets = [b for b in CURVE_BUCKETS if before.curve[b] != after.curve[b]]
    cut = [card for card in saved.cards if deck.count(card) < saved.count(card)]
    added = [card for card in deck.cards if deck.count(card) > saved.count(card)]
    names = card_names(ctx.conn, [*cut, *added])

    def listed(cards: list[UUID], deck_a: DeckState, deck_b: DeckState) -> str:
        parts = []
        for card in cards:
            n = abs(deck_a.count(card) - deck_b.count(card))
            parts.append(f"{names.get(card, card)}" + (f" x{n}" if n > 1 else ""))
        return ", ".join(parts) or "nothing"

    return [
        f"Compared with the saved version {version} (check the request is met):",
        f"Lands: {change(before.lands, after.lands)}",
        "Average mana value: "
        + change(f"{before.average_mana_value:.2f}", f"{after.average_mana_value:.2f}")
        + " (nonland cards besides the commander)",
        "Mana curve changes: "
        + (", ".join(f"{b}: {before.curve[b]} → {after.curve[b]}" for b in buckets) or "none"),
        "Role changes: "
        + (", ".join(f"{r} {before.roles[r]} → {after.roles[r]}" for r in changed_roles) or "none")
        + f". Untagged: {change(before.untagged, after.untagged)}",
        f"Cut: {listed(cut, saved, deck)}",
        f"Added: {listed(added, deck, saved)}",
    ]


def _analyze_deck(ctx: ToolContext, args: AnalyzeDeckArgs) -> Output:
    pool = _require_pool(ctx)
    deck = ctx.draft
    saved = load_deck(ctx.conn, ctx.deck_id) if ctx.deck_id is not None else None
    if deck is None and saved is not None:
        deck = saved.state
    if deck is None:
        raise ToolError("There's no deck to analyze yet: propose one with propose_deck first.")

    ids = [deck.commander, *deck.cards]
    commander = ctx.conn.execute(
        "SELECT name, color_identity FROM cards WHERE oracle_id = %s", (deck.commander,)
    ).fetchone()
    profile = _profile(ctx, deck)
    identity = "".join(c for c in "WUBRG" if commander and c in commander[1]) or "colorless"
    violations = validate(deck, load_card_facts(ctx.conn, ids), pool.cards).violations
    lines = [
        f"{deck.total} cards (a Commander deck has exactly 100, the commander included).",
        f"Commander: {commander[0] if commander else deck.commander}",
        f"Color identity: {identity}",
        f"Lands: {profile.lands}",
        "Mana curve (nonland cards besides the commander): "
        + ", ".join(f"{b}: {profile.curve[b]}" for b in CURVE_BUCKETS if profile.curve[b]),
        "Roles (nonland cards besides the commander): "
        + (", ".join(f"{role} {n}" for role, n in profile.roles.most_common()) or "none")
        + f". Untagged: {profile.untagged}",
    ]
    if violations:
        lines.append(f"Not legal yet, {len(violations)} problems:")
        lines += [_problem_line(v.model_dump(mode="json")) for v in violations]
    else:
        lines.append("Legal: no problems found.")
    # In a refine: the proposal against the saved version it changes (#136).
    if saved is not None and saved.state is not None and saved.version is not None:
        # A refine (#136): the proposal against the saved version, each card, and the pool.
        if deck is not saved.state:
            lines += _comparison(ctx, saved.state, saved.version, deck)
        lines += _refine_detail(ctx, deck)
    return Output(untrusted("\n".join(lines)))


def _within(identity: str, commander_identity: str) -> bool:
    return all(color in commander_identity for color in identity)


def _find_by_role(ctx: ToolContext, args: FindByRoleArgs) -> Output:
    pool = _require_pool(ctx)
    deck_id = _require_deck_id(ctx)
    saved = load_deck(ctx.conn, deck_id)
    deck = ctx.draft or (saved.state if saved else None)
    if deck is None:
        raise ToolError("There's no saved deck to search for.")
    cards = _pool_cards(ctx)
    identity = cards[deck.commander].color_identity if deck.commander in cards else "WUBRG"
    roles = load_roles(ctx.conn, pool.cards)
    found = sorted(
        (
            card
            for oracle_id, tagged in roles.items()
            if args.role in tagged.roles
            and (card := cards.get(oracle_id)) is not None
            and _within(card.color_identity, identity)
            and (args.mana_value_max is None or card.mana_value <= args.mana_value_max)
        ),
        key=lambda card: (card.mana_value, card.name),
    )[: args.k]
    if not found:
        return Output(f"No pool cards within the commander's colors are tagged {args.role}.")
    lines = []
    for number, card in enumerate(found, 1):
        ctx.seen_cards.add(card.oracle_id)
        where = "in the deck" if deck.count(card.oracle_id) else "not in the deck"
        lines.append(
            f"{number}. {card.name} | {card.mana_cost or 'no cost'} | {card.type_line} | "
            f"{_one_line(card.oracle_text)} | {where}"
        )
    return Output(
        f"{len(found)} pool cards tagged {args.role}, within the commander's colors, cheapest "
        f"first:\n{untrusted(chr(10).join(lines))}"
    )


def _refine_detail(ctx: ToolContext, deck: DeckState) -> list[str]:
    """For a refine: each nonland card with its mana value and roles, and what the pool has
    left by role, so "the weakest cards" and "add more X" have something to go on."""
    pool = _require_pool(ctx)
    cards = _pool_cards(ctx)
    names = card_names(ctx.conn, [deck.commander, *deck.cards])
    facts = {
        row[0]: (float(row[1] or 0), row[2], row[3])
        for row in ctx.conn.execute(
            "SELECT oracle_id, cmc, type_line, color_identity FROM cards WHERE oracle_id = ANY(%s)",
            (list({deck.commander, *deck.cards, *pool.cards}),),
        ).fetchall()
    }

    def is_land(card: UUID) -> bool:
        return "Land" in facts[card][1].split("—")[0] if card in facts else False

    roles = load_roles(ctx.conn, list({*deck.cards, *pool.cards}))
    nonland = sorted(
        (card for card in deck.cards if not is_land(card)),
        key=lambda card: (-facts[card][0], names.get(card, "")),
    )
    lines = ["Nonland cards, most expensive first (mana value): roles"]
    for card in nonland:
        tags = ", ".join(roles[card].roles) if card in roles and roles[card].roles else "no role"
        copies = deck.count(card)
        lines.append(f"{copies} {names.get(card, card)} ({facts[card][0]:g}): {tags}")
    identity = "".join(facts[deck.commander][2]) if deck.commander in facts else "WUBRG"
    left: Counter[str] = Counter()
    for card in pool.cards:
        if deck.count(card) or is_land(card) or card not in cards:
            continue
        if not _within(cards[card].color_identity, identity):
            continue
        for role in roles[card].roles if card in roles else []:
            left[role] += 1
    lines.append(
        "In the pool but not the deck, within the commander's colors, by role: "
        + (", ".join(f"{role} {n}" for role, n in left.most_common()) or "none")
    )
    return lines


def _propose_deck(ctx: ToolContext, args: ProposeDeckArgs) -> Output:
    pool = _require_pool(ctx)
    deck_id = _require_deck_id(ctx)
    problems: list[dict[str, Any]] = []
    commander = _find(ctx, args.commander)
    if commander is None:
        problems.append(
            {"code": "unknown_card", "message": "Commander: " + _not_found(ctx, args.commander)}
        )
    counts, unresolved = _resolve(ctx, args.cards)
    problems += unresolved

    problems += _citation_problems(ctx, args.citations)

    deck = DeckState.new(commander.oracle_id, counts) if commander else None
    if deck is not None:
        ctx.draft = deck
        facts = load_card_facts(ctx.conn, [deck.commander, *deck.cards])
        problems += [
            v.model_dump(mode="json") for v in validate(deck, facts, pool.cards).violations
        ]
    proposal = save_proposal(
        ctx.conn,
        run_id=ctx.run_id,
        deck_id=deck_id,
        kind="deck",
        payload={
            "request": args.model_dump(mode="json"),
            "deck": deck.to_dict() if deck else None,
        },
        # Applying refuses if the deck has moved on since (None: no version yet).
        base_version=_saved_version(ctx, deck_id),
        rationale=args.rationale,
        citations=args.citations,
        problems=problems,
    )
    ctx.proposals.append(proposal)
    return _proposal_output(len(ctx.proposals), problems)


def _propose_changes(ctx: ToolContext, args: ProposeChangesArgs) -> Output:
    pool = _require_pool(ctx)
    deck_id = _require_deck_id(ctx)
    saved = load_deck(ctx.conn, deck_id)
    if saved is None or saved.state is None or saved.version is None:
        raise ToolError("This deck has no saved version yet, so there's nothing to change.")

    adds, problems = _resolve(ctx, args.add)
    removes, unresolved = _resolve(ctx, args.remove)
    problems += unresolved + _citation_problems(ctx, args.citations)
    deck = saved.state
    names = {card.oracle_id: card.name for card in _pool_cards(ctx).values()}
    for card, count in removes.items():
        held = deck.count(card)
        if count > held:
            problems.append(
                {
                    "code": "not_in_deck",
                    "message": f"Can't remove {count} {names[card]}: the deck has "
                    f"{held or 'none'}.",
                }
            )
        else:
            deck = deck.without_card(card, count)
    for card, count in adds.items():
        deck = deck.with_card(card, count)

    ctx.draft = deck
    facts = load_card_facts(ctx.conn, [deck.commander, *deck.cards])
    problems += [v.model_dump(mode="json") for v in validate(deck, facts, pool.cards).violations]
    for goal in check_goals(ctx.conn, args.goals, saved.state, deck):
        if not goal["met"]:
            problems.append(
                {
                    "code": "goal_not_met",
                    "message": f"Goal not met: {goal['goal']} ({goal['actual']}).",
                }
            )
    proposal = save_proposal(
        ctx.conn,
        run_id=ctx.run_id,
        deck_id=deck_id,
        kind="changes",
        payload={
            "request": args.model_dump(mode="json"),
            "add": {str(card): n for card, n in adds.items()},
            "remove": {str(card): n for card, n in removes.items()},
            "deck": deck.to_dict(),
        },
        base_version=saved.version,
        rationale=args.rationale,
        citations=args.citations,
        problems=problems,
    )
    ctx.proposals.append(proposal)
    return _proposal_output(len(ctx.proposals), problems)


# --- the registry --------------------------------------------------------------------


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[Arguments]
    run: Callable[[ToolContext, Any], Output]
    tasks: frozenset[Task]

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            input_schema=self.args.model_json_schema(),
        )


DECK_TASKS: frozenset[Task] = frozenset({"draft", "refine"})

TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        Tool(
            "search_pool",
            "Search the user's card pool by meaning and keywords, with optional exact filters. "
            "Returns each card's cost, type, rules text, copies owned and role tags. Only "
            "cards in the pool can go in the deck.",
            SearchPoolArgs,
            _search_pool,
            DECK_TASKS,
        ),
        Tool(
            "get_card",
            "Look up one card in the user's pool by name: its full rules text, color "
            "identity, copies owned and role tags.",
            GetCardArgs,
            _get_card,
            DECK_TASKS,
        ),
        Tool(
            "search_rules",
            "Search the Magic Comprehensive Rules. Returns numbered rules to cite.",
            SearchRulesArgs,
            _search_rules,
            frozenset({"draft", "refine", "rules"}),
        ),
        Tool(
            "analyze_deck",
            "Analyze the deck as your latest proposal would leave it (or the saved deck): "
            "size, color identity, lands, mana curve, role counts and every rules problem.",
            AnalyzeDeckArgs,
            _analyze_deck,
            DECK_TASKS,
        ),
        Tool(
            "find_by_role",
            "List the user's pool cards that have a role tag (removal, ramp, card draw...), "
            "within the commander's colors, cheapest first, each marked as in the deck or not.",
            FindByRoleArgs,
            _find_by_role,
            frozenset({"refine"}),
        ),
        Tool(
            "propose_deck",
            "Propose a complete deck: a commander and 99 other cards from the pool (basic "
            "lands are free). Code checks it against the Commander rules: a legal deck "
            "waits for the user's approval, an illegal one comes back with every problem "
            "to fix.",
            ProposeDeckArgs,
            _propose_deck,
            frozenset({"draft"}),
        ),
        Tool(
            "propose_changes",
            "Propose cards to add to and remove from the saved deck. Code checks the "
            "resulting deck: a legal result waits for the user's approval, an illegal one "
            "comes back with every problem to fix. Each proposal starts again from the "
            "saved deck.",
            ProposeChangesArgs,
            _propose_changes,
            frozenset({"refine"}),
        ),
    )
}


def tool_specs(task: Task) -> tuple[ToolSpec, ...]:
    """The tools a task offers, as the model sees them."""
    return tuple(tool.spec() for tool in TOOLS.values() if task in tool.tasks)


def execute(ctx: ToolContext, call: ToolCall, *, turn: int) -> ToolResult:
    """Run one tool call and record it. Always returns a result for the model."""
    started = time.perf_counter()
    content, outcome = _run(ctx, call)
    latency_ms = (time.perf_counter() - started) * 1000
    record_tool_call(
        ctx.conn,
        ctx.run_id,
        turn=turn,
        tool_call_id=call.id,
        tool=call.name,
        arguments=call.arguments,
        result=content,
        outcome=outcome,
        latency_ms=latency_ms,
    )
    log.info(
        "tool_call",
        run_id=str(ctx.run_id),
        turn=turn,
        tool=call.name,
        outcome=outcome,
        latency_ms=round(latency_ms, 1),
    )
    return ToolResult(tool_call_id=call.id, content=content, is_error=outcome != "ok")


def _run(ctx: ToolContext, call: ToolCall) -> tuple[str, ToolOutcome]:
    offered = [name for name, tool in TOOLS.items() if ctx.task in tool.tasks]
    tool = TOOLS.get(call.name)
    if tool is None:
        return f"Unknown tool {call.name!r}. Available tools: {', '.join(offered)}.", "error"
    if ctx.task not in tool.tasks:
        return (
            f"The {call.name} tool isn't available when {TASK_WORDS[ctx.task]}. "
            f"Available tools: {', '.join(offered)}.",
            "error",
        )
    try:
        args = tool.args.model_validate(call.arguments)
    except ValidationError as exc:
        return f"Invalid arguments for {call.name}: {_describe(exc)}. Fix them and retry.", "error"
    try:
        with ctx.conn.transaction():
            output = tool.run(ctx, args)
    except ToolError as exc:
        return str(exc), "error"
    except Exception:
        log.exception("tool_failed", run_id=str(ctx.run_id), tool=call.name)
        return (
            f"{call.name} failed with an internal error. Try again, or carry on without it.",
            "error",
        )
    return output.content, output.outcome


# --- helpers -------------------------------------------------------------------------


def _require_pool(ctx: ToolContext) -> Pool:
    if ctx.pool is None:
        raise ToolError("This task has no card pool.")
    return ctx.pool


def _require_deck_id(ctx: ToolContext) -> UUID:
    if ctx.deck_id is None:
        raise ToolError("This task has no deck to propose for.")
    return ctx.deck_id


def _pool_cards(ctx: ToolContext) -> dict[UUID, PoolCard]:
    """The pool's cards and the free basic lands, loaded once per run."""
    if ctx._cards is None:
        pool = _require_pool(ctx)
        rows = ctx.conn.execute(
            """
            SELECT oracle_id, name, mana_cost, cmc, type_line, oracle_text, color_identity
            FROM cards
            WHERE removed_at IS NULL
              AND (oracle_id = ANY(%s) OR (name = ANY(%s) AND commander_legality = 'legal'))
            """,
            (list(pool.cards), sorted(FREE_BASICS)),
        ).fetchall()
        ctx._cards = {
            row[0]: PoolCard(
                oracle_id=row[0],
                name=row[1],
                mana_cost=row[2],
                mana_value=row[3],
                type_line=row[4],
                oracle_text=row[5],
                color_identity="".join(c for c in "WUBRG" if c in row[6]),
                owned=pool.cards.get(row[0], 0),
            )
            for row in rows
        }
    return ctx._cards


def _name_keys(name: str) -> list[str]:
    """Lookup keys for a name: exact, then without punctuation; for a multi-face
    card, its front face's too."""
    exact = fold_name(name).replace(" / ", " // ")
    keys = [exact, loose_name(exact)]
    if " // " in exact:
        front = exact.split(" // ")[0]
        keys += [front, loose_name(front)]
    return keys


def _find(ctx: ToolContext, name: str) -> PoolCard | None:
    """A pool card or free basic by name, forgiving case, accents and punctuation."""
    if ctx._names is None:
        ctx._names = {}
        for card in _pool_cards(ctx).values():
            for key in _name_keys(card.name):
                ctx._names.setdefault(key, card)
    exact = fold_name(name).replace(" / ", " // ")
    for key in (exact, loose_name(exact)):
        if key in ctx._names:
            return ctx._names[key]
    return None


def _not_found(ctx: ToolContext, name: str) -> str:
    """Why `name` matched nothing, with the closest pool names if there are any."""
    exact = fold_name(name).replace(" / ", " // ")
    in_catalogue = ctx.conn.execute(
        """
        SELECT name FROM cards
        WHERE removed_at IS NULL AND %s IN (name_key, front_face_key)
        LIMIT 1
        """,
        (exact,),
    ).fetchone()
    if in_catalogue is not None:
        return f"{in_catalogue[0]} is not in the user's pool, so it can't be used."
    names = {fold_name(card.name): card.name for card in _pool_cards(ctx).values()}
    close = difflib.get_close_matches(exact, names, n=3, cutoff=0.6)
    message = f"No card named {name!r} is in the user's pool."
    if close:
        message += " Did you mean: " + "; ".join(names[key] for key in close) + "?"
    return message


def _resolve(
    ctx: ToolContext, entries: Iterable[CardCount]
) -> tuple[dict[UUID, int], list[dict[str, Any]]]:
    """Card counts by ID, and a problem for each name that matched nothing."""
    counts: dict[UUID, int] = {}
    problems: list[dict[str, Any]] = []
    for entry in entries:
        card = _find(ctx, entry.name)
        if card is None:
            problems.append({"code": "unknown_card", "message": _not_found(ctx, entry.name)})
        else:
            counts[card.oracle_id] = counts.get(card.oracle_id, 0) + entry.count
    return counts, problems


def _saved_version(ctx: ToolContext, deck_id: UUID) -> int | None:
    saved = load_deck(ctx.conn, deck_id)
    return saved.version if saved else None


def _citation_problems(ctx: ToolContext, citations: Iterable[str]) -> list[dict[str, Any]]:
    """A problem for each citation this run's tool results never showed (RAG-3).

    A citation is a rule number ("903.5c") or a card name. Citing only what a
    tool returned means a rationale can't lean on a rule or card the model
    recalled, or invented, rather than looked up.
    """
    problems = []
    for citation in citations:
        cited = citation.strip().rstrip(".")
        if RULE_NUMBER.fullmatch(cited):
            shown = cited in ctx.seen_rules
        else:
            card = _find(ctx, cited)
            shown = card is not None and card.oracle_id in ctx.seen_cards
        if not shown:
            problems.append(
                {
                    "code": "unsupported_citation",
                    "message": f"Citation '{citation}' isn't from this run's tool results: "
                    "cite only rule numbers and cards a tool showed you.",
                }
            )
    return problems


def _problem_line(problem: dict[str, Any]) -> str:
    rule = problem.get("rule") or ""
    citation = f" (rule {rule})" if rule[:1].isdigit() else ""
    return f"- {problem['message']}{citation}"


def _proposal_output(number: int, problems: list[dict[str, Any]]) -> Output:
    """What the model is told about its proposal. It's numbered within the run
    rather than by its ID, so a recorded run replays with identical requests."""
    if not problems:
        return Output(
            f"Proposal {number} is legal and is now waiting for the user's approval. "
            "Nothing changes until they approve it."
        )
    lines = "\n".join(_problem_line(problem) for problem in problems)
    return Output(
        f"Proposal {number} was rejected with {len(problems)} problems. Fix every one "
        f"and propose again.\n{untrusted(lines)}",
        "rejected",
    )


def _one_line(text: str) -> str:
    return text.replace("\n", " / ")


def _describe(error: ValidationError) -> str:
    """Pydantic's errors in a line, without echoing the input back."""
    return "; ".join(
        f"{'.'.join(str(part) for part in e['loc']) or 'arguments'}: {e['msg']}"
        for e in error.errors()
    )
