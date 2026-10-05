"""Pools, agent runs and rules answers over HTTP (ENG-5).

Drafts and refines take a minute or so, so they don't hold the request open:
the route records the run as started, answers 202 with where to find it, and
the agent runs in a background task on its own connection, under the same
trace ID as the request that started it (OBS-2). `GET /runs/{id}` shows its
progress and, when it ends, its proposals. Rules answers take seconds and are
answered directly.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException, Response, status
from pydantic import BaseModel, Field

from mtg_deck_advisor.agent.flows import (
    NotFoundError,
    NotReadyError,
    PreparedRun,
    answer_rules_question,
    commander_candidates,
    execute_run,
    import_pool,
    prepare_draft,
    prepare_redraft,
    prepare_refine,
)
from mtg_deck_advisor.agent.runs import RunStatus, fail_run, load_run, run_proposals
from mtg_deck_advisor.api.services import AppSettings, Connection, Services, ServicesFactory
from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.deck.store import DeckName, list_pools, load_pool
from mtg_deck_advisor.observability.tracing import current_trace_id, traced

log = structlog.get_logger(__name__)

router = APIRouter()


# --- models -----------------------------------------------------------------------------


class PoolSubmission(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    format: Literal["text", "csv"] = Field(
        default="text", description="A pasted list (`1 Sol Ring` lines) or a CSV export."
    )
    content: str = Field(min_length=1, max_length=2_000_000)


class PoolCreated(BaseModel):
    pool_id: UUID
    cards: int
    distinct: int
    unresolved: list[str] = Field(description="Names that matched no card, or several.")
    problems: list[str] = Field(description="Lines that couldn't be read.")


class PoolListing(BaseModel):
    id: UUID
    name: str
    source: Literal["text", "csv"]
    cards: int
    distinct: int
    created_at: datetime


class Commander(BaseModel):
    name: str
    color_identity: str


class PoolDetail(BaseModel):
    id: UUID
    name: str
    source: Literal["text", "csv"]
    cards: int
    distinct: int
    commanders: list[Commander] = Field(description="The pool's cards that can lead a deck.")


class DraftRequest(BaseModel):
    request: str = Field(
        default="", max_length=2000, description="What the user wants, if anything."
    )
    name: DeckName | None = Field(
        default=None,
        description='A name for the new deck. Without one it\'s called "New <pool> deck" '
        "until its first version is saved, then takes its commander's name.",
    )


class RedraftRequest(BaseModel):
    request: str = Field(
        default="", max_length=2000, description="What the user wants instead, if anything."
    )


class RefineRequest(BaseModel):
    request: str = Field(min_length=1, max_length=2000, description="The change wanted.")


class RunStarted(BaseModel):
    run_id: UUID
    deck_id: UUID | None
    status: RunStatus


class ProposalSummary(BaseModel):
    id: UUID
    kind: Literal["deck", "changes"]
    status: str


class RunView(BaseModel):
    id: UUID
    task: Literal["draft", "refine", "rules"]
    status: RunStatus
    pool_id: UUID | None
    deck_id: UUID | None
    model: str
    turns: int
    cost_usd: float
    final_text: str | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None
    proposals: list[ProposalSummary]


class RulesQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class CitedRule(BaseModel):
    number: str
    text: str


class RulesAnswerView(BaseModel):
    question: str
    found: bool
    answer: str | None = Field(description="Shown only when every cited rule was retrieved.")
    citations: list[CitedRule]
    reason: str = Field(description="Why there's no answer; empty when found.")
    run_id: UUID
    cost_usd: float


# --- pools -------------------------------------------------------------------------------


@router.post("/pools", status_code=status.HTTP_201_CREATED, tags=["pools"])
def submit_pool(submission: PoolSubmission, conn: Connection) -> PoolCreated:
    """Read a card pool, match its names to cards, and save what matched."""
    imported = import_pool(conn, submission.content, name=submission.name, source=submission.format)
    conn.commit()
    pool = load_pool(conn, imported.pool_id)
    cards = pool.cards if pool else {}
    return PoolCreated(
        pool_id=imported.pool_id,
        cards=sum(cards.values()),
        distinct=len(cards),
        unresolved=imported.unresolved,
        problems=imported.problems,
    )


@router.get("/pools", tags=["pools"])
def get_pools(conn: Connection) -> list[PoolListing]:
    """Every submitted pool, oldest first, with its card counts."""
    return [PoolListing(**vars(pool)) for pool in list_pools(conn)]


@router.get("/pools/{pool_id}", tags=["pools"], responses={404: {}})
def get_pool(pool_id: UUID, conn: Connection) -> PoolDetail:
    """A pool's card counts and the cards in it that can be a commander."""
    pool = load_pool(conn, pool_id)
    if pool is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"there is no pool {pool_id}")
    return PoolDetail(
        id=pool.id,
        name=pool.name,
        source=pool.source,
        cards=sum(pool.cards.values()),
        distinct=len(pool.cards),
        commanders=[
            Commander(name=name, color_identity=identity)
            for name, identity in commander_candidates(conn, pool)
        ],
    )


# --- runs ----------------------------------------------------------------------------------


@router.post(
    "/pools/{pool_id}/drafts",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["runs"],
    responses={404: {}},
)
def start_draft(
    pool_id: UUID,
    body: DraftRequest,
    conn: Connection,
    services: Services,
    settings: AppSettings,
    background: BackgroundTasks,
    response: Response,
) -> RunStarted:
    """Start drafting a new deck from the pool. Poll the run for the result."""
    try:
        prepared = prepare_draft(
            conn, services(conn).client.model, pool_id, request=body.request, name=body.name
        )
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return _start(conn, prepared, settings, services, background, response)


@router.post(
    "/decks/{deck_id}/drafts",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["runs"],
    responses={404: {}, 409: {"description": "The deck has a saved version, or is archived."}},
)
def start_redraft(
    deck_id: UUID,
    body: RedraftRequest,
    conn: Connection,
    services: Services,
    settings: AppSettings,
    background: BackgroundTasks,
    response: Response,
) -> RunStarted:
    """Draft again into a deck with nothing saved yet, such as after rejecting its first draft."""
    model = services(conn).client.model
    try:
        prepared = prepare_redraft(conn, model, deck_id, request=body.request)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except NotReadyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _start(conn, prepared, settings, services, background, response)


@router.post(
    "/decks/{deck_id}/refinements",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["runs"],
    responses={404: {}, 409: {}},
)
def start_refine(
    deck_id: UUID,
    body: RefineRequest,
    conn: Connection,
    services: Services,
    settings: AppSettings,
    background: BackgroundTasks,
    response: Response,
) -> RunStarted:
    """Start proposing changes to the deck's latest saved version. Poll the run for the result."""
    try:
        prepared = prepare_refine(conn, services(conn).client.model, deck_id, request=body.request)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except NotReadyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _start(conn, prepared, settings, services, background, response)


@router.get("/runs/{run_id}", tags=["runs"], responses={404: {}})
def get_run(run_id: UUID, conn: Connection) -> RunView:
    """A run's progress or outcome: status, turns, cost, final text, and its proposals."""
    run = load_run(conn, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"there is no run {run_id}")
    fields = {key: value for key, value in vars(run).items() if key != "trace_id"}
    return RunView(
        **fields,
        proposals=[ProposalSummary(**vars(p)) for p in run_proposals(conn, run_id)],
    )


@router.post("/rules/answers", tags=["rules"])
def answer_rules(body: RulesQuestion, conn: Connection, services: Services) -> RulesAnswerView:
    """Answer a rules question from the Comprehensive Rules, citing them, or say not found."""
    result = answer_rules_question(services(conn), body.question)
    conn.commit()
    return RulesAnswerView(
        question=result.question,
        found=result.found,
        answer=result.answer,
        citations=[CitedRule(number=c.number, text=c.text) for c in result.citations],
        reason=result.reason,
        run_id=result.run.run_id,
        cost_usd=result.run.cost_usd,
    )


def _start(
    conn: Connection,
    prepared: PreparedRun,
    settings: Settings,
    services: ServicesFactory,
    background: BackgroundTasks,
    response: Response,
) -> RunStarted:
    # Committed before answering: the background run reads it on its own connection.
    conn.commit()
    background.add_task(_execute, settings, services, prepared, current_trace_id())
    response.headers["Location"] = f"/runs/{prepared.run_id}"
    return RunStarted(run_id=prepared.run_id, deck_id=prepared.deck_id, status="running")


def _execute(
    settings: Settings, services: ServicesFactory, prepared: PreparedRun, trace_id: str | None
) -> None:
    """Run the agent after the response has gone, recording any failure on the run."""
    with traced(trace_id), connect(settings) as conn:
        try:
            execute_run(services(conn), prepared)
        except Exception as exc:
            log.exception("background_run_failed", run_id=str(prepared.run_id))
            conn.rollback()
            fail_run(conn, prepared.run_id, repr(exc))
