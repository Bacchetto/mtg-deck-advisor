from alembic.script import ScriptDirectory

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.db.migrate import alembic_config, upgrade


def schema_snapshot(settings: Settings) -> list[tuple[str, ...]]:
    """Every extension, table and column in the database, in a stable order."""
    with connect(settings) as conn:
        extensions = conn.execute(
            "SELECT 'extension', extname, extversion FROM pg_extension ORDER BY extname"
        ).fetchall()
        columns = conn.execute(
            """
            SELECT 'column', table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position
            """
        ).fetchall()
    return [*extensions, *columns]


def head_revision(settings: Settings) -> str | None:
    return ScriptDirectory.from_config(alembic_config(settings)).get_current_head()


def test_connect_can_run_a_query(settings: Settings) -> None:
    with connect(settings) as conn:
        row = conn.execute("SELECT 1").fetchone()

    assert row == (1,)


def test_migrating_a_fresh_database_enables_pgvector(settings: Settings) -> None:
    upgrade(settings)

    with connect(settings) as conn:
        vector = conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'").fetchone()
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()

    assert vector is not None
    assert version == (head_revision(settings),)


def test_migrating_twice_changes_nothing(settings: Settings) -> None:
    upgrade(settings)
    before = schema_snapshot(settings)

    upgrade(settings)

    assert schema_snapshot(settings) == before
