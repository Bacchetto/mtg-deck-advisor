"""The HTTP API (ENG-5). OpenAPI documentation is served at /docs.

Run it with `python -m mtg_deck_advisor.api` (see `__main__.py`), which sets
up logging before the server starts. create_app is a factory, so settings are
read when the server starts rather than when this module is imported.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import psycopg
import structlog
from fastapi import Depends, FastAPI, Response, status
from pydantic import BaseModel

from mtg_deck_advisor.agent.flows import build_services
from mtg_deck_advisor.agent.runs import interrupt_running_runs
from mtg_deck_advisor.api import routes_proposals, routes_runs
from mtg_deck_advisor.api.services import ServicesFactory, app_settings
from mtg_deck_advisor.api.tracing import TraceMiddleware
from mtg_deck_advisor.config import Settings, get_settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.observability.logging import configure_logging

log = structlog.get_logger(__name__)


class HealthStatus(BaseModel):
    status: Literal["ok", "unavailable"]
    database: Literal["ok", "unreachable"]


def database_is_reachable(settings: Annotated[Settings, Depends(app_settings)]) -> bool:
    """Whether the database answers a trivial query within the connect timeout."""
    try:
        with connect(settings) as conn:
            conn.execute("SELECT 1")
    except psycopg.Error:
        log.warning("database_unreachable", exc_info=True)
        return False
    return True


def create_app(
    settings: Settings | None = None, *, services: ServicesFactory | None = None
) -> FastAPI:
    """Build the application.

    Tests pass their own settings and a services factory with a scripted model;
    the server reads the environment and builds the real services from it.
    """
    settings = settings or get_settings()
    resolved = settings

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        configure_logging(resolved)
        log.info("app_started", environment=resolved.environment)
        _recover_interrupted_runs(resolved)
        yield

    app = FastAPI(
        title="MTG Deck Advisor",
        version="0.1.0",
        description="Build a Commander deck from your card pool with an agent that only "
        "proposes: code checks every proposal, and nothing changes without your approval.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.services = services or (lambda conn: build_services(conn, resolved))
    app.add_middleware(TraceMiddleware)
    app.include_router(routes_runs.router)
    app.include_router(routes_proposals.router)

    @app.get(
        "/health",
        responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthStatus}},
    )
    def health(
        response: Response,
        database_ok: Annotated[bool, Depends(database_is_reachable)],
    ) -> HealthStatus:
        """Liveness and database reachability, for container and load balancer checks (DEP-3)."""
        if database_ok:
            return HealthStatus(status="ok", database="ok")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthStatus(status="unavailable", database="unreachable")

    return app


def _recover_interrupted_runs(settings: Settings) -> None:
    """Runs still `running` when a server starts were cut off by a stop: mark them."""
    try:
        with connect(settings) as conn:
            if count := interrupt_running_runs(conn):
                log.warning("interrupted_runs_recovered", count=count)
    except psycopg.Error:
        log.warning("interrupted_runs_not_checked", exc_info=True)
