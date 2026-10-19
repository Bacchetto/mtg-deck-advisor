"""Attacks on the code rather than the model: SQL injection and oversized input (#160).

These go through no model, so they belong in free scripted tests rather than
live injection cases (#133). Every query is parameterized (ADR 0002) and every
input has a size limit; these tests prove both hold at each entry point.
"""

# Fixtures imported from other test modules are named again as test parameters,
# which is how pytest injects them; ruff reads that as a redefinition.
# ruff: noqa: F811

from collections.abc import Callable

import psycopg
from fastapi.testclient import TestClient

from mtg_deck_advisor.agent.flows import import_pool
from mtg_deck_advisor.ingestion.card_ingestion import apply_cards
from mtg_deck_advisor.ingestion.cards import normalise
from mtg_deck_advisor.ingestion.pool import MAX_ENTRIES, MAX_INPUT_BYTES, parse_text
from tests.integration.test_agent_flows import conn  # noqa: F401
from tests.integration.test_agent_tools import (  # noqa: F401
    ATRAXA,
    LEGAL_CARDS,
    card_id,
    fixture_cards,
    loaded,
    session_for,
)
from tests.integration.test_api_runs import POOL_TEXT, app_for  # noqa: F401

SQL = "Sol Ring'); DROP TABLE cards; --"
TABLES = ("cards", "rules", "pools", "pool_cards", "decks", "deck_versions", "card_roles")


def intact(conn: psycopg.Connection) -> dict[str, int]:
    counts = {}
    for table in TABLES:
        row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608 (constant names)
        counts[table] = row[0] if row else -1
    return counts


# --- SQL injection: text that looks like SQL is stored and searched as text ------------------


def test_sql_like_card_names_and_text_are_ingested_as_text(conn: psycopg.Connection) -> None:
    before = intact(conn)
    raw = {
        "oracle_id": "11111111-2222-3333-4444-555555555555",
        "name": SQL,
        "layout": "normal",
        "mana_cost": "{1}",
        "cmc": 1.0,
        "type_line": "Artifact",
        "oracle_text": "'; DELETE FROM rules; /* {T}: Add {C}. */ --",
        "colors": [],
        "color_identity": [],
        "legalities": {"commander": "legal"},
    }
    record = normalise(raw)
    assert record is not None

    apply_cards(conn, [*fixture_cards(), record])

    after = intact(conn)
    assert after["cards"] == before["cards"] + 1 and after["rules"] == before["rules"]
    stored = conn.execute(
        "SELECT name, oracle_text FROM cards WHERE oracle_id = %s", (raw["oracle_id"],)
    ).fetchone()
    assert stored == (SQL, raw["oracle_text"])


def test_sql_like_pool_lines_and_names_are_stored_as_text(conn: psycopg.Connection) -> None:
    before = intact(conn)

    imported = import_pool(conn, f"1 {SQL}\n1 Sol Ring\n", name=SQL, source="text")

    after = intact(conn)
    assert imported.unresolved == [SQL]  # no card has that name; nothing ran
    assert after["cards"] == before["cards"] and after["pools"] == before["pools"] + 1
    row = conn.execute("SELECT name FROM pools WHERE id = %s", (imported.pool_id,)).fetchone()
    assert row == (SQL,)


def test_sql_like_tool_arguments_are_searched_as_text(conn: psycopg.Connection) -> None:
    session = session_for(conn, "draft")
    before = intact(conn)

    results = [
        session.call("search_pool", query=SQL),
        session.call("get_card", name=SQL),
        session.call("search_rules", question=SQL),
        session.call("search_pool", query="x", types=[SQL], color_identity_within="WUBG"),
        session.call(
            "propose_deck",
            commander=SQL,
            cards=[*LEGAL_CARDS, {"name": SQL}],
            rationale=SQL,
            citations=[SQL],
        ),
    ]

    assert intact(conn) == before
    assert all(result.content for result in results)  # each answered; none crashed
    assert results[-1].is_error  # the proposal is rejected, naming the unknown cards


def test_sql_like_deck_names_are_stored_as_text(conn: psycopg.Connection) -> None:
    from mtg_deck_advisor.deck.store import create_deck, list_decks, rename_deck

    imported = import_pool(conn, "1 Sol Ring\n", name="Binder", source="text")
    before = intact(conn)

    deck_id = create_deck(conn, imported.pool_id, name=SQL)
    new_name = "Rats'; UPDATE decks SET name = 'pwned'; --"
    old_name = rename_deck(conn, deck_id, new_name)  # returns the name it replaced

    after = intact(conn)
    assert after == before | {"decks": before["decks"] + 1}
    assert old_name == SQL
    assert [d.name for d in list_decks(conn) if d.id == deck_id] == [new_name]


# --- size: every entry point refuses or caps oversized input -------------------------------


def test_oversized_api_input_is_refused_before_it_runs(
    app_for: Callable[..., TestClient],
) -> None:
    client = app_for()
    pool = client.post("/pools", json={"name": "Binder", "content": POOL_TEXT}).json()

    refused = [
        client.post("/pools", json={"name": "x" * 201, "content": POOL_TEXT}),
        client.post("/pools", json={"name": "Big", "content": "1 Sol Ring\n" * 200_000}),
        client.post(f"/pools/{pool['pool_id']}/drafts", json={"request": "x" * 2001}),
        client.post("/rules/answers", json={"question": "x" * 1001}),
    ]

    assert [r.status_code for r in refused] == [422, 422, 422, 422]


def test_the_pool_parser_caps_size_entries_and_names() -> None:
    too_big = parse_text("1 Sol Ring\n" * (MAX_INPUT_BYTES // 10))
    too_many = parse_text("".join(f"1 Card {n}\n" for n in range(MAX_ENTRIES + 10)))
    long_name = parse_text(f"1 {'x' * 5000}\n")

    assert too_big.entries == [] and "too large" in too_big.problems[0].message
    assert len(too_many.entries) == MAX_ENTRIES
    assert long_name.entries == [] and long_name.problems
    assert all(len(p.text) <= 100 for p in long_name.problems)  # echoed text is cut too


def test_oversized_tool_arguments_come_back_as_errors(conn: psycopg.Connection) -> None:
    session = session_for(conn, "draft")

    results = [
        session.call("search_pool", query="x" * 301),
        session.call("search_pool", query="ramp", k=1000),
        session.call("search_rules", question="x" * 501),
        session.call("propose_deck", commander=ATRAXA, cards=[{"name": "x"}] * 151, rationale="r"),
    ]

    assert all(result.is_error for result in results)
    assert all("Invalid arguments" in result.content for result in results)
