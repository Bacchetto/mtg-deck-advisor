"""What the API's routes share: a database connection per request, and the agent's services.

The services are built by a factory kept on `app.state`, one per run, so each
run gets its own model client and cost cap. Tests pass a factory with a
scripted model; the server uses `build_services` from settings.
"""

from collections.abc import Callable, Iterator
from typing import Annotated

import psycopg
from fastapi import Depends, Request

from mtg_deck_advisor.agent.flows import AgentServices
from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect

type ServicesFactory = Callable[[psycopg.Connection], AgentServices]


def app_settings(request: Request) -> Settings:
    """The settings this app was created with."""
    settings: Settings = request.app.state.settings
    return settings


def services_factory(request: Request) -> ServicesFactory:
    factory: ServicesFactory = request.app.state.services
    return factory


def database(settings: Annotated[Settings, Depends(app_settings)]) -> Iterator[psycopg.Connection]:
    """A connection for one request. Routes commit what they write before answering."""
    with connect(settings) as conn:
        yield conn


Connection = Annotated[psycopg.Connection, Depends(database)]
Services = Annotated[ServicesFactory, Depends(services_factory)]
AppSettings = Annotated[Settings, Depends(app_settings)]
