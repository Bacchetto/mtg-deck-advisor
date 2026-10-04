"""The agent's tools on a real database, fake embedder and reranker (AGT-1, AGT-4, GRD-3)."""

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import psycopg
import pytest

from mtg_deck_advisor.agent.runs import start_run, tool_calls_for
from mtg_deck_advisor.agent.tools import Task, ToolContext, execute, tool_specs
from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.db.migrate import upgrade
from mtg_deck_advisor.deck.state import DeckState
from mtg_deck_advisor.deck.store import create_deck, create_pool, load_pool, save_version
from mtg_deck_advisor.guardrails.audit import audit_entries
from mtg_deck_advisor.ingestion.card_ingestion import apply_cards
from mtg_deck_advisor.ingestion.cards import CardRecord, normalise
from mtg_deck_advisor.ingestion.rules import parse_rules
from mtg_deck_advisor.ingestion.rules_ingestion import apply_rules
from mtg_deck_advisor.llm.fake import FakeEmbedder
from mtg_deck_advisor.llm.roles import PROMPT_VERSION, CardRoles, save_roles
from mtg_deck_advisor.llm.types import ToolCall, ToolResult
from mtg_deck_advisor.retrieval.embeddings import embed_cards, embed_rules

FIXTURES = Path(__file__).parent.parent / "fixtures"
CARD_FILES = (
    "oracle_cards_sample.jsonl",
    "oracle_cards_punctuation.jsonl",
    "oracle_cards_deckbuilding.jsonl",
)

ATRAXA = "Atraxa, Praetors' Voice"
RATS = "Relentless Rats"
COHORT = "Lim-Dûl's Cohort"
BONECRUSHER = "Bonecrusher Giant // Stomp"
DELVER = "Delver of Secrets // Insectile Aberration"
POOL = {ATRAXA: 1, RATS: 60, "Sol Ring": 1, "Mox Jet": 1, COHORT: 1, BONECRUSHER: 1, DELVER: 1}
# Atraxa, 60 rats, Sol Ring, the Cohort and 37 free basics: a legal 100.
BASICS = {"Plains": 10, "Island": 9, "Swamp": 9, "Forest": 9}
LEGAL_CARDS = [
    {"name": RATS, "count": 60},
    {"name": "Sol Ring", "count": 1},
    {"name": COHORT, "count": 1},
    *({"name": name, "count": count} for name, count in BASICS.items()),
]


def fixture_cards() -> list[CardRecord]:
    lines = [
        line
        for name in CARD_FILES
        for line in (FIXTURES / "scryfall" / name).read_text(encoding="utf-8").splitlines()
    ]
    return [record for line in lines if (record := normalise(json.loads(line))) is not None]


def card_id(name: str) -> UUID:
    matches = [card for card in fixture_cards() if card.name == name]
    if len(matches) > 1:  # a joke card can share a real card's name
        matches = [card for card in matches if card.commander_legality == "legal"]
    (card,) = matches
    return card.oracle_id


class ReversingReranker:
    """Puts the first stage's order backwards, so its use is visible."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def rerank(self, query: str, candidates: Sequence[tuple[UUID, str]]) -> list[UUID]:
        self.queries.append(query)
        return [key for key, _ in reversed(candidates)]


class BrokenEmbedder(FakeEmbedder):
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise ConnectionError("the embedder is down")


@pytest.fixture
def loaded(settings: Settings) -> Settings:
    upgrade(settings)
    rules = (FIXTURES / "rules" / "comprehensive_rules_excerpt.txt").read_text(encoding="utf-8")
    with connect(settings) as conn:
        apply_cards(conn, fixture_cards())
        apply_rules(conn, parse_rules(rules).rules)
        embed_cards(conn, FakeEmbedder())
        embed_rules(conn, FakeEmbedder())
        hashes: dict[UUID, str] = dict(
            conn.execute("SELECT oracle_id, content_hash FROM cards").fetchall()
        )
        save_roles(
            conn,
            [
                CardRoles(
                    oracle_id=card_id("Sol Ring"),
                    roles=["ramp"],
                    reason="Adds mana.",
                    content_hash=hashes[card_id("Sol Ring")],
                    prompt_version=PROMPT_VERSION,
                    model="fake",
                ),
                CardRoles(
                    oracle_id=card_id(RATS),
                    roles=["finisher"],
                    reason="Grows with copies.",
                    content_hash=hashes[card_id(RATS)],
                    prompt_version=PROMPT_VERSION,
                    model="fake",
                ),
            ],
        )
    return settings


@dataclass
class Session:
    """A run's tool context, and a way to call its tools."""

    context: ToolContext
    turn: int = 0

    def call(self, tool: str, **arguments: object) -> ToolResult:
        self.turn += 1
        call = ToolCall(id=f"toolu_{self.turn}", name=tool, arguments=dict(arguments))
        return execute(self.context, call, turn=self.turn)


def session_for(
    conn: psycopg.Connection,
    task: Task,
    *,
    embedder: FakeEmbedder | None = None,
    reranker: ReversingReranker | None = None,
    base: DeckState | None = None,
) -> Session:
    pool_id = create_pool(conn, {card_id(n): c for n, c in POOL.items()}, name="P", source="text")
    deck_id = create_deck(conn, pool_id, name="Atraxa")
    if base is not None:
        save_version(conn, deck_id, base)
    run_id = start_run(conn, task, "claude-sonnet-5-5", pool_id=pool_id, deck_id=deck_id)
    return Session(
        ToolContext(
            conn=conn,
            embedder=embedder or FakeEmbedder(),
            reranker=reranker,
            run_id=run_id,
            task=task,
            pool=load_pool(conn, pool_id),
            deck_id=deck_id,
        )
    )


@pytest.fixture
def conn(loaded: Settings) -> Iterator[psycopg.Connection]:
    with connect(loaded) as connection:
        yield connection


def body(result: ToolResult) -> str:
    """The delimited data in a result."""
    assert result.content.count("<untrusted>") == 1, result.content
    return result.content.split("<untrusted>\n", 1)[1].split("\n</untrusted>", 1)[0]


# --- the registry ---------------------------------------------------------------


def test_each_task_offers_only_its_own_tools() -> None:
    def names(task: Task) -> list[str]:
        return [spec.name for spec in tool_specs(task)]

    assert names("draft") == [
        "search_pool",
        "get_card",
        "search_rules",
        "analyze_deck",
        "propose_deck",
    ]
    assert names("refine") == [
        "search_pool",
        "get_card",
        "search_rules",
        "analyze_deck",
        "propose_changes",
    ]
    assert names("rules") == ["search_rules"]


def test_tool_schemas_come_from_the_argument_models() -> None:
    specs = {spec.name: spec for spec in tool_specs("draft")}

    search = specs["search_pool"].input_schema
    assert search["type"] == "object"
    assert search["required"] == ["query"]
    assert "description" in search["properties"]["query"]
    assert all(spec.description for spec in specs.values())


# --- search_pool ------------------------------------------------------------------


def test_search_pool_finds_only_pool_cards_inside_delimiters(conn: psycopg.Connection) -> None:
    session = session_for(conn, "draft")

    result = session.call("search_pool", query="add colorless mana artifact", k=25)

    assert not result.is_error
    assert result.tool_call_id == "toolu_1"
    found = body(result)
    assert "Sol Ring" in found
    assert "Teferi" not in found  # in the catalogue, not in the pool
    assert card_id("Sol Ring") in session.context.seen_cards


def test_search_pool_applies_exact_filters(conn: psycopg.Connection) -> None:
    session = session_for(conn, "draft")

    found = body(session.call("search_pool", query="creature", types=["Creature"], k=25))

    assert RATS in found and COHORT in found
    assert "Sol Ring" not in found


def test_search_pool_uses_the_reranker_when_there_is_one(conn: psycopg.Connection) -> None:
    reranker = ReversingReranker()
    plain = body(session_for(conn, "draft").call("search_pool", query="creature", k=25))
    reranked = body(
        session_for(conn, "draft", reranker=reranker).call("search_pool", query="creature", k=25)
    )

    assert reranker.queries == ["creature"]
    assert plain.splitlines()[0].split(". ", 1)[1] != reranked.splitlines()[0].split(". ", 1)[1]


def test_a_search_with_no_results_says_so(conn: psycopg.Connection) -> None:
    session = session_for(conn, "draft")

    result = session.call("search_pool", query="anything", types=["Battle"])

    assert not result.is_error
    assert "No cards" in result.content


# --- get_card ---------------------------------------------------------------------


def test_get_card_shows_the_card_by_any_reasonable_name(conn: psycopg.Connection) -> None:
    session = session_for(conn, "draft")

    for name in ("Sol Ring", "sol ring", "Delver of Secrets", "lim-dul's cohort"):
        result = session.call("get_card", name=name)
        assert not result.is_error, (name, result.content)

    sol_ring = body(session.call("get_card", name="Sol Ring"))
    assert "{T}: Add {C}{C}." in sol_ring
    assert "Owned: 1" in sol_ring
    assert "Roles: ramp" in sol_ring
    assert card_id("Sol Ring") in session.context.seen_cards


def test_get_card_for_a_misspelt_name_suggests_pool_cards(conn: psycopg.Connection) -> None:
    session = session_for(conn, "draft")

    result = session.call("get_card", name="Sol Rnig")

    assert result.is_error
    assert "Did you mean" in result.content and "Sol Ring" in result.content


def test_get_card_for_a_card_outside_the_pool_is_an_error(conn: psycopg.Connection) -> None:
    session = session_for(conn, "draft")

    result = session.call("get_card", name="Teferi, Temporal Archmage")

    assert result.is_error
    assert "not in the user's pool" in result.content


# --- search_rules -----------------------------------------------------------------


def test_search_rules_returns_citable_rules(conn: psycopg.Connection) -> None:
    session = session_for(conn, "rules")

    result = session.call("search_rules", question="color identity commander", k=3)

    assert not result.is_error
    found = body(result)
    assert "903.4" in found
    assert "903.4" in session.context.seen_rules


# --- errors go back to the model (AGT-4) -----------------------------------------


def test_an_unknown_tool_is_an_error_result_listing_the_real_ones(
    conn: psycopg.Connection,
) -> None:
    session = session_for(conn, "draft")

    result = session.call("export_deck", format="text")

    assert result.is_error
    assert "Unknown tool 'export_deck'" in result.content
    assert "propose_deck" in result.content


def test_bad_arguments_are_an_error_result_naming_the_problem(conn: psycopg.Connection) -> None:
    session = session_for(conn, "draft")

    missing = session.call("search_pool", k=5)
    too_many = session.call("search_pool", query="ramp", k=500)
    extra = session.call("get_card", name="Sol Ring", approve=True)

    assert missing.is_error and "query" in missing.content
    assert too_many.is_error and "k" in too_many.content
    assert extra.is_error and "approve" in extra.content


def test_a_tool_from_another_task_is_refused(conn: psycopg.Connection) -> None:
    session = session_for(conn, "rules")

    result = session.call("propose_deck", commander=ATRAXA, cards=[], rationale="r")

    assert result.is_error
    assert "isn't available" in result.content


def test_a_failing_tool_is_an_error_result_and_the_run_carries_on(
    conn: psycopg.Connection,
) -> None:
    session = session_for(conn, "draft", embedder=BrokenEmbedder())

    failed = session.call("search_pool", query="ramp")
    after = session.call("get_card", name="Sol Ring")

    assert failed.is_error and "search_pool failed" in failed.content
    assert "the embedder is down" not in failed.content  # internals stay internal
    assert not after.is_error


# --- proposals ----------------------------------------------------------------------


def test_a_legal_deck_proposal_waits_for_the_user(conn: psycopg.Connection) -> None:
    session = session_for(conn, "draft")
    session.call("search_rules", question="color identity commander", k=3)

    result = session.call(
        "propose_deck",
        commander=ATRAXA,
        cards=LEGAL_CARDS,
        rationale="Rats and ramp.",
        citations=["903.4"],
    )

    assert not result.is_error, result.content
    assert "waiting for the user's approval" in result.content
    (proposal,) = session.context.proposals
    row = conn.execute(
        "SELECT kind, status, rationale, citations, payload->'deck'->>'commander' "
        "FROM proposals WHERE id = %s",
        (proposal,),
    ).fetchone()
    assert row == ("deck", "pending", "Rats and ramp.", ["903.4"], str(card_id(ATRAXA)))
    assert session.context.draft is not None and session.context.draft.total == 100
    (entry,) = audit_entries(conn, f"proposal:{proposal}")
    assert (entry.actor, entry.action) == ("agent", "propose")


def test_an_illegal_deck_is_rejected_with_every_reason(conn: psycopg.Connection) -> None:
    session = session_for(conn, "draft")

    result = session.call(
        "propose_deck",
        commander=ATRAXA,
        cards=[*LEGAL_CARDS[1:], {"name": BONECRUSHER}, {"name": "Mox Jet"}],
        rationale="r",
    )

    assert result.is_error
    problems = body(result)
    assert "Bonecrusher Giant" in problems and "903.5c" in problems  # outside the colors
    assert "Mox Jet" in problems and "banned" in problems.lower()
    assert "903.5a" in problems  # 42 cards, not 100
    (proposal,) = session.context.proposals
    status = conn.execute("SELECT status FROM proposals WHERE id = %s", (proposal,)).fetchone()
    assert status == ("invalid",)


def test_a_proposal_naming_cards_outside_the_pool_is_rejected(conn: psycopg.Connection) -> None:
    session = session_for(conn, "draft")

    result = session.call(
        "propose_deck",
        commander=ATRAXA,
        cards=[*LEGAL_CARDS, {"name": "Teferi, Temporal Archmage"}, {"name": "Sol Rnig"}],
        rationale="r",
    )

    assert result.is_error
    problems = body(result)
    assert "Teferi, Temporal Archmage" in problems
    assert "Sol Rnig" in problems and "Sol Ring" in problems  # with a suggestion


def test_changes_are_proposed_against_the_latest_version(conn: psycopg.Connection) -> None:
    rats, sol_ring, cohort = card_id(RATS), card_id("Sol Ring"), card_id(COHORT)
    basics = {card_id(name): count for name, count in BASICS.items()}
    base = DeckState.new(card_id(ATRAXA), {rats: 60, sol_ring: 1, cohort: 1, **basics})
    session = session_for(conn, "refine", base=base)

    result = session.call(
        "propose_changes",
        add=[{"name": "Delver of Secrets"}],
        remove=[{"name": "Island"}],
        rationale="A cheap flier.",
    )

    assert not result.is_error, result.content
    (proposal,) = session.context.proposals
    row = conn.execute(
        "SELECT kind, status, base_version FROM proposals WHERE id = %s", (proposal,)
    ).fetchone()
    assert row == ("changes", "pending", 1)
    draft = session.context.draft
    assert draft is not None
    assert draft.count(card_id(DELVER)) == 1 and draft.count(card_id("Island")) == 8


def test_removing_a_card_the_deck_lacks_is_rejected(conn: psycopg.Connection) -> None:
    base = DeckState.new(card_id(ATRAXA), {card_id(RATS): 99})
    session = session_for(conn, "refine", base=base)

    result = session.call("propose_changes", add=[], remove=[{"name": "Sol Ring"}], rationale="r")

    assert result.is_error
    assert "Sol Ring" in body(result)


def test_changes_need_a_saved_deck(conn: psycopg.Connection) -> None:
    session = session_for(conn, "refine")

    result = session.call("propose_changes", add=[{"name": "Sol Ring"}], rationale="r")

    assert result.is_error
    assert "no saved version" in result.content


# --- analyze_deck -------------------------------------------------------------------


def test_analyze_deck_needs_a_draft_or_a_saved_deck(conn: psycopg.Connection) -> None:
    result = session_for(conn, "draft").call("analyze_deck")

    assert result.is_error
    assert "propose_deck" in result.content


def test_analyze_deck_reports_roles_curve_colors_and_validity(conn: psycopg.Connection) -> None:
    session = session_for(conn, "draft")
    session.call("propose_deck", commander=ATRAXA, cards=LEGAL_CARDS, rationale="r")

    analysis = body(session.call("analyze_deck"))

    assert "100 cards" in analysis
    assert "Color identity: WUBG" in analysis
    assert "Lands: 37" in analysis
    assert "ramp 1" in analysis and "finisher 60" in analysis
    assert "Untagged: 1" in analysis  # the Cohort
    assert "3: 61" in analysis  # mana value 3: the rats and the Cohort
    assert "Legal" in analysis


# --- recording (OBS-1) --------------------------------------------------------------


def test_every_call_is_recorded_with_arguments_outcome_and_latency(
    conn: psycopg.Connection,
) -> None:
    session = session_for(conn, "draft")
    session.call("get_card", name="Sol Ring")
    session.call("get_card", name="Nope")
    session.call("export_deck")

    recorded = tool_calls_for(conn, session.context.run_id)

    assert [(r.turn, r.tool, r.outcome) for r in recorded] == [
        (1, "get_card", "ok"),
        (2, "get_card", "error"),
        (3, "export_deck", "error"),
    ]
    assert recorded[0].arguments == {"name": "Sol Ring"}
    assert recorded[0].tool_call_id == "toolu_1"
    assert recorded[0].result is not None and "Sol Ring" in recorded[0].result
    assert all(r.latency_ms is not None and r.latency_ms >= 0 for r in recorded)


def test_a_rejected_proposal_is_recorded_as_rejected(conn: psycopg.Connection) -> None:
    session = session_for(conn, "draft")
    session.call("propose_deck", commander=ATRAXA, cards=[], rationale="r")

    (recorded,) = tool_calls_for(conn, session.context.run_id)

    assert recorded.outcome == "rejected"


# --- citations (RAG-3) ----------------------------------------------------------------


def test_citations_must_come_from_this_runs_tool_results(conn: psycopg.Connection) -> None:
    session = session_for(conn, "draft")
    session.call("search_rules", question="color identity commander", k=3)
    session.call("get_card", name="Sol Ring")

    shown = session.call(
        "propose_deck",
        commander=ATRAXA,
        cards=LEGAL_CARDS,
        rationale="r",
        citations=["903.4", "903.4.", "Sol Ring", "sol ring"],
    )
    unseen = session.call(
        "propose_deck",
        commander=ATRAXA,
        cards=LEGAL_CARDS,
        rationale="r",
        citations=["704.5z", "Lim-Dûl's Cohort", "999.9"],
    )

    assert not shown.is_error, shown.content
    assert unseen.is_error
    problems = body(unseen)
    for citation in ("704.5z", "Lim-Dûl's Cohort", "999.9"):
        assert f"'{citation}'" in problems
    status = conn.execute(
        "SELECT status FROM proposals WHERE id = %s", (session.context.proposals[-1],)
    ).fetchone()
    assert status == ("invalid",)
