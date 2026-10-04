"""The state the agent works on, owned by code (AGT-3), and the audit log (GRD-5)."""

import json
import uuid
from pathlib import Path

import psycopg
import pytest

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.db.migrate import upgrade
from mtg_deck_advisor.deck.state import DeckState
from mtg_deck_advisor.deck.store import (
    create_deck,
    create_pool,
    load_deck,
    load_pool,
    save_version,
)
from mtg_deck_advisor.guardrails.audit import audit_entries, record_audit
from mtg_deck_advisor.ingestion.card_ingestion import apply_cards
from mtg_deck_advisor.ingestion.cards import CardRecord, normalise
from mtg_deck_advisor.observability.tracing import traced

FIXTURES = Path(__file__).parent.parent / "fixtures" / "scryfall"


def fixture_cards() -> list[CardRecord]:
    lines = [
        line
        for name in ("oracle_cards_sample.jsonl", "oracle_cards_punctuation.jsonl")
        for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines()
    ]
    return [record for line in lines if (record := normalise(json.loads(line))) is not None]


@pytest.fixture
def loaded(settings: Settings) -> Settings:
    upgrade(settings)
    with connect(settings) as conn:
        apply_cards(conn, fixture_cards())
    return settings


def ids(*names: str) -> list[uuid.UUID]:
    by_name = {card.name: card.oracle_id for card in fixture_cards()}
    return [by_name[name] for name in names]


# --- pools and decks ------------------------------------------------------------


def test_a_pool_round_trips(loaded: Settings) -> None:
    atraxa, sol_ring = ids("Atraxa, Praetors' Voice", "Sol Ring")

    with connect(loaded) as conn:
        pool_id = create_pool(conn, {atraxa: 1, sol_ring: 2}, name="Binder", source="text")
        pool = load_pool(conn, pool_id)

    assert pool is not None
    assert pool.id == pool_id
    assert pool.name == "Binder"
    assert pool.source == "text"
    assert dict(pool.cards) == {atraxa: 1, sol_ring: 2}


def test_an_unknown_pool_is_none(loaded: Settings) -> None:
    with connect(loaded) as conn:
        assert load_pool(conn, uuid.uuid4()) is None


def test_a_pool_only_holds_known_cards_in_positive_counts(loaded: Settings) -> None:
    (sol_ring,) = ids("Sol Ring")

    with connect(loaded) as conn, pytest.raises(psycopg.errors.ForeignKeyViolation):
        create_pool(conn, {uuid.uuid4(): 1}, name="Bad", source="text")
    with connect(loaded) as conn, pytest.raises(psycopg.errors.CheckViolation):
        create_pool(conn, {sol_ring: 0}, name="Bad", source="text")


def test_each_saved_deck_state_is_a_new_numbered_version(loaded: Settings) -> None:
    atraxa, sol_ring, mox_jet = ids("Atraxa, Praetors' Voice", "Sol Ring", "Mox Jet")
    first = DeckState.new(atraxa, {sol_ring: 1})
    second = first.with_card(mox_jet)

    with connect(loaded) as conn:
        pool_id = create_pool(conn, {atraxa: 1, sol_ring: 1, mox_jet: 1}, name="P", source="csv")
        deck_id = create_deck(conn, pool_id, name="Atraxa superfriends")
        empty = load_deck(conn, deck_id)
        v1 = save_version(conn, deck_id, first)
        v2 = save_version(conn, deck_id, second)
        latest = load_deck(conn, deck_id)
        earlier = load_deck(conn, deck_id, version=1)

    assert empty is not None and empty.version is None and empty.state is None
    assert (v1, v2) == (1, 2)
    assert latest is not None and latest.version == 2 and latest.state == second
    assert latest.pool_id == pool_id and latest.name == "Atraxa superfriends"
    assert earlier is not None and earlier.version == 1 and earlier.state == first


def test_an_unknown_deck_or_version_is_none(loaded: Settings) -> None:
    (atraxa,) = ids("Atraxa, Praetors' Voice")
    with connect(loaded) as conn:
        pool_id = create_pool(conn, {atraxa: 1}, name="P", source="text")
        deck_id = create_deck(conn, pool_id, name="D")

        assert load_deck(conn, uuid.uuid4()) is None
        assert load_deck(conn, deck_id, version=3) is None


def test_deck_versions_cannot_be_changed_or_deleted(loaded: Settings) -> None:
    atraxa, sol_ring = ids("Atraxa, Praetors' Voice", "Sol Ring")
    with connect(loaded) as conn:
        pool_id = create_pool(conn, {atraxa: 1, sol_ring: 1}, name="P", source="text")
        deck_id = create_deck(conn, pool_id, name="D")
        save_version(conn, deck_id, DeckState.new(atraxa, {sol_ring: 1}))

    with connect(loaded) as conn, pytest.raises(psycopg.errors.RaiseException, match="immutable"):
        conn.execute("UPDATE deck_versions SET cards = '{}'::jsonb")
    with connect(loaded) as conn, pytest.raises(psycopg.errors.RaiseException, match="immutable"):
        conn.execute("DELETE FROM deck_versions")


# --- the audit log ------------------------------------------------------------------


def test_an_audit_entry_records_actor_action_subject_and_trace_id(loaded: Settings) -> None:
    with traced() as trace_id, connect(loaded) as conn:
        record_audit(conn, "user", "approve", "proposal:42", {"note": "looks good"})
        entries = audit_entries(conn, "proposal:42")

    (entry,) = entries
    assert (entry.actor, entry.action, entry.subject) == ("user", "approve", "proposal:42")
    assert entry.details == {"note": "looks good"}
    assert entry.trace_id == trace_id
    assert entry.at is not None


def test_audit_entries_come_back_in_the_order_they_were_written(loaded: Settings) -> None:
    with connect(loaded) as conn:
        record_audit(conn, "agent", "propose", "deck:1")
        record_audit(conn, "user", "approve", "deck:1")
        record_audit(conn, "system", "apply", "deck:1")
        record_audit(conn, "system", "apply", "deck:2")
        entries = audit_entries(conn, "deck:1")

    assert [entry.action for entry in entries] == ["propose", "approve", "apply"]


def test_only_known_actors_can_be_recorded(loaded: Settings) -> None:
    with connect(loaded) as conn, pytest.raises(psycopg.errors.CheckViolation):
        record_audit(conn, "model", "approve", "deck:1")  # type: ignore[arg-type]


def test_the_audit_log_is_append_only(loaded: Settings) -> None:
    with connect(loaded) as conn:
        record_audit(conn, "user", "approve", "proposal:1")

    for statement in (
        "UPDATE audit_log SET actor = 'system'",
        "DELETE FROM audit_log",
        "TRUNCATE audit_log",
    ):
        with (
            connect(loaded) as conn,
            pytest.raises(psycopg.errors.RaiseException, match="append-only"),
        ):
            conn.execute(statement)

    with connect(loaded) as conn:
        assert len(audit_entries(conn, "proposal:1")) == 1


# --- runs, tool calls and proposals -----------------------------------------------


def test_runs_tool_calls_and_proposals_are_constrained_to_known_values(
    loaded: Settings,
) -> None:
    (atraxa,) = ids("Atraxa, Praetors' Voice")
    with connect(loaded) as conn:
        pool_id = create_pool(conn, {atraxa: 1}, name="P", source="text")
        deck_id = create_deck(conn, pool_id, name="D")
        (run_id,) = conn.execute(
            """
            INSERT INTO agent_runs (task, pool_id, deck_id, model, status, trace_id)
            VALUES ('draft', %s, %s, 'claude-sonnet-5-5', 'running', 't-1') RETURNING id
            """,
            (pool_id, deck_id),
        ).fetchone() or (None,)
        conn.execute(
            """
            INSERT INTO tool_calls (run_id, turn, tool_call_id, tool, arguments, result, outcome)
            VALUES (%s, 1, 'toolu_1', 'search_pool', '{"query": "ramp"}', '1. Sol Ring', 'ok')
            """,
            (run_id,),
        )
        conn.execute(
            """
            INSERT INTO proposals (run_id, deck_id, kind, payload, rationale, status)
            VALUES (%s, %s, 'deck', '{}', 'Ramp first.', 'pending')
            """,
            (run_id, deck_id),
        )

    bad = [
        ("UPDATE agent_runs SET status = 'paused'", psycopg.errors.CheckViolation),
        ("UPDATE tool_calls SET outcome = 'maybe'", psycopg.errors.CheckViolation),
        ("UPDATE proposals SET status = 'done'", psycopg.errors.CheckViolation),
        ("UPDATE proposals SET kind = 'export'", psycopg.errors.CheckViolation),
        (
            "INSERT INTO tool_calls (run_id, turn, tool_call_id, tool, arguments, outcome) "
            "VALUES (gen_random_uuid(), 1, 'x', 'get_card', '{}', 'ok')",
            psycopg.errors.ForeignKeyViolation,
        ),
    ]
    for statement, error in bad:
        with connect(loaded) as conn, pytest.raises(error):
            conn.execute(statement)
