import uuid
from collections.abc import Iterator

import psycopg
import pytest
from testcontainers.community.postgres import PostgresContainer

from mtg_deck_advisor.config import Settings

# Keep in step with the `db` service in docker-compose.yml, so tests run
# against exactly the database the app uses locally.
PGVECTOR_IMAGE = "pgvector/pgvector:0.8.6-pg16-trixie"


@pytest.fixture(scope="session")
def postgres() -> Iterator[PostgresContainer]:
    """One real Postgres + pgvector container for the whole test session."""
    with PostgresContainer(PGVECTOR_IMAGE, driver=None) as container:
        yield container


@pytest.fixture
def database_url(postgres: PostgresContainer) -> Iterator[str]:
    """A fresh, empty database per test, dropped afterwards.

    Starting a container per test would take seconds each; creating a
    database inside the shared container takes milliseconds and still gives
    every test a clean slate.
    """
    admin_url = postgres.get_connection_url()
    name = f"test_{uuid.uuid4().hex}"
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield admin_url.rsplit("/", 1)[0] + f"/{name}"
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.fixture
def settings(database_url: str) -> Settings:
    return Settings(_env_file=None, database_url=database_url)
