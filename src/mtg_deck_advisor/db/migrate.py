"""Apply database migrations: `python -m mtg_deck_advisor.db.migrate`.

Migrations run as their own step before the app starts (a one-shot Compose
service locally, a pre-deploy job in the cloud), never from the app itself,
so several app instances starting together never race to migrate. See
ADR 0003.
"""

from pathlib import Path

import structlog
from alembic import command
from alembic.config import Config

from mtg_deck_advisor.config import Settings, get_settings
from mtg_deck_advisor.observability.logging import configure_logging

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def sqlalchemy_url(database_url: str) -> str:
    """The same URL, with the scheme SQLAlchemy needs to pick psycopg 3.

    The app uses plain libpq URLs (postgresql://...). SQLAlchemy reads
    postgresql:// as "use psycopg2", which is not installed.
    """
    for scheme in ("postgresql://", "postgres://"):
        if database_url.startswith(scheme):
            return "postgresql+psycopg://" + database_url.removeprefix(scheme)
    return database_url


def alembic_config(settings: Settings) -> Config:
    """Alembic's configuration, built in code instead of read from alembic.ini.

    One source of truth for the database URL: Settings. The URL goes in
    `attributes`, not `set_main_option`, because main options pass through
    ConfigParser, which reads % as interpolation syntax and would mangle a
    URL-encoded password.
    """
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.attributes["database_url"] = sqlalchemy_url(settings.database_url.get_secret_value())
    return config


def upgrade(settings: Settings) -> None:
    """Apply every migration not yet applied. Does nothing when already at head."""
    command.upgrade(alembic_config(settings), "head")


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    log = structlog.get_logger(__name__)
    log.info("migrations_starting")
    upgrade(settings)
    log.info("migrations_finished")


if __name__ == "__main__":
    main()
