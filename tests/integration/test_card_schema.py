from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.db.migrate import upgrade


def columns(settings: Settings, table: str) -> set[str]:
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
            (table,),
        ).fetchall()
    return {name for (name,) in rows}


def test_migrations_create_the_cards_table(settings: Settings) -> None:
    upgrade(settings)

    assert {
        "oracle_id",
        "name",
        "name_key",
        "front_face_key",
        "oracle_text",
        "color_identity",
        "commander_legality",
        "content_hash",
        "removed_at",
    } <= columns(settings, "cards")


def test_migrations_create_the_ingestion_runs_table(settings: Settings) -> None:
    upgrade(settings)

    assert {
        "source",
        "source_version",
        "trace_id",
        "added",
        "updated",
        "unchanged",
        "removed",
    } <= columns(settings, "ingestion_runs")


def test_commander_legality_only_accepts_known_values(settings: Settings) -> None:
    upgrade(settings)

    with connect(settings) as conn:
        allowed = conn.execute(
            """
            SELECT pg_get_constraintdef(oid) FROM pg_constraint
            WHERE conrelid = 'cards'::regclass AND contype = 'c'
            """
        ).fetchall()

    assert any("'banned'" in definition for (definition,) in allowed)
