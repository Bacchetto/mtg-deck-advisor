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
from fastapi import Depends, FastAPI, Request, Response, status
from pydantic import BaseModel

from mtg_deck_advisor.api.tracing import TraceMiddleware
from mtg_deck_advisor.config import Settings, get_settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.observability.logging import configure_logging

log = structlog.get_logger(__name__)


class HealthStatus(BaseModel):
    status: Literal["ok", "unavailable"]
    database: Literal["ok", "unreachable"]


def app_settings(request: Request) -> Settings:
    """The settings this app was created with."""
    settings: Settings = request.app.state.settings
    return settings


def database_is_reachable(settings: Annotated[Settings, Depends(app_settings)]) -> bool:
    """Whether the database answers a trivial query within the connect timeout."""
    try:
        with connect(settings) as conn:
            conn.execute("SELECT 1")
    except psycopg.Error:
        log.warning("database_unreachable", exc_info=True)
        return False
    return True


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application. Tests pass their own settings; the server reads the environment."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings)
        log.info("app_started", environment=settings.environment)
        yield

    app = FastAPI(title="MTG Deck Advisor", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(TraceMiddleware)

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
