"""Opening database connections.

Queries are written as explicit SQL against psycopg 3, with no ORM, so every
query the application runs is visible in the code that runs it. See ADR 0002.
"""

import psycopg
from psycopg.rows import TupleRow

from mtg_deck_advisor.config import Settings

# Fail fast when the database is unreachable instead of hanging a request or a
# health check on the operating system's much longer TCP timeout.
CONNECT_TIMEOUT_SECONDS = 5


def connect(settings: Settings) -> psycopg.Connection[TupleRow]:
    """Open a new connection. Use it as a context manager so it is always closed."""
    return psycopg.connect(
        settings.database_url.get_secret_value(),
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
    )
