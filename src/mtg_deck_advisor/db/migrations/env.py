"""Alembic's entry point, run by `alembic.command` for every migration command.

There is no `target_metadata`: the schema is written by hand in each
migration as raw SQL, and never generated from Python models (ADR 0003).
Logging is not configured here, so Alembic's lines go through the app's
structured logging.
"""

from alembic import context
from sqlalchemy import create_engine, pool

from mtg_deck_advisor.db.connection import CONNECT_TIMEOUT_SECONDS

if context.is_offline_mode():
    raise RuntimeError("Offline (SQL script) migrations are not supported; run against a database.")

engine = create_engine(
    context.config.attributes["database_url"],
    # A migration run opens one connection and exits, so pooling connections
    # would only leave them open for nothing.
    poolclass=pool.NullPool,
    # The same fail-fast timeout as the app's own connections. Without it an
    # unreachable address waits out the operating system's TCP timeout
    # (about two minutes on Windows) before Alembic reports anything.
    connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
)

with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=None)
    # One transaction for the whole run. Postgres has transactional DDL, so a
    # migration that fails halfway leaves nothing half-applied.
    with context.begin_transaction():
        context.run_migrations()
