"""The embedding snapshot and the free eval gate, against real Postgres (#119).

The snapshot carries cards, rules and their embeddings, plus the embeddings
of the eval queries, so CI can run the real search code without the local
embedding model. These tests use fixture cards and the hashing FakeEmbedder.
"""

# Fixtures imported from other test modules are named again as test parameters,
# which is how pytest injects them; ruff reads that as a redefinition.
# ruff: noqa: F811

import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from testcontainers.community.postgres import PostgresContainer

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.db.migrate import upgrade
from mtg_deck_advisor.evaluation.gate import (
    ValidatorCase,
    check,
    retrieval_metrics,
    validator_metrics,
)
from mtg_deck_advisor.evaluation.retrieval_set import (
    CardQuery,
    EvalSet,
    QueryFilters,
    RuleQuestion,
)
from mtg_deck_advisor.evaluation.snapshot import export_snapshot, load_snapshot
from mtg_deck_advisor.llm.fake import FakeEmbedder
from mtg_deck_advisor.retrieval import search
from mtg_deck_advisor.retrieval.search import search_cards, search_rules
from tests.integration.test_agent_tools import (  # noqa: F401
    ATRAXA,
    COHORT,
    LEGAL_CARDS,
    RATS,
    loaded,
)

SET = EvalSet(
    name="tiny",
    card_queries=[
        CardQuery(
            id="C1",
            kind="paraphrase",
            scope="catalogue",
            query="artifact that adds colorless mana",
            filters=QueryFilters(),
            relevant=["Sol Ring"],
            notes="",
        ),
        CardQuery(
            id="C2",
            kind="name",
            scope="catalogue",
            query="Relentless Rats",
            filters=QueryFilters(),
            relevant=[RATS],
            notes="",
        ),
    ],
    rule_questions=[
        RuleQuestion(
            id="R1",
            kind="commander",
            question="Does reminder text count toward color identity?",
            relevant=["903.4c"],
            notes="",
        ),
    ],
    pool=[],
)


@pytest.fixture
def second_url(postgres: PostgresContainer) -> Iterator[str]:
    """Another empty database in the same container, to load a snapshot into."""
    admin_url = postgres.get_connection_url()
    name = f"snapshot_{uuid.uuid4().hex}"
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield admin_url.rsplit("/", 1)[0] + f"/{name}"
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


def queries(eval_set: EvalSet) -> list[tuple[str, str]]:
    return [("cards", q.query) for q in eval_set.card_queries] + [
        ("rules", q.question) for q in eval_set.rule_questions
    ]


def exported(loaded: Settings, directory: Path) -> None:
    with connect(loaded) as conn:
        card_ids = [row[0] for row in conn.execute("SELECT oracle_id FROM cards")]
        export_snapshot(conn, FakeEmbedder(), directory, card_ids=card_ids, queries=queries(SET))


def test_a_loaded_snapshot_searches_exactly_like_its_source(
    loaded: Settings, second_url: str, tmp_path: Path
) -> None:
    exported(loaded, tmp_path)
    copy = Settings(_env_file=None, database_url=second_url)
    upgrade(copy)

    with connect(copy) as conn:
        embedder = load_snapshot(conn, tmp_path)
        loaded_cards = [h.name for h in search_cards(conn, embedder, SET.card_queries[0].query)]
        loaded_rules = [
            h.number for h in search_rules(conn, embedder, SET.rule_questions[0].question)
        ]
    with connect(loaded) as conn:
        source_cards = [
            h.name for h in search_cards(conn, FakeEmbedder(), SET.card_queries[0].query)
        ]
        source_rules = [
            h.number for h in search_rules(conn, FakeEmbedder(), SET.rule_questions[0].question)
        ]

    assert embedder.model == "fake-embedder"
    assert loaded_cards == source_cards and "Sol Ring" in loaded_cards
    assert loaded_rules == source_rules


def test_the_snapshot_records_what_made_it(loaded: Settings, tmp_path: Path) -> None:
    exported(loaded, tmp_path)

    manifest = (tmp_path / "manifest.json").read_text(encoding="utf-8")

    assert '"model": "fake-embedder"' in manifest
    assert '"queries": 3' in manifest


def test_retrieval_metrics_cover_cards_and_rules(loaded: Settings) -> None:
    with connect(loaded) as conn:
        metrics = retrieval_metrics(conn, FakeEmbedder(), SET)

    assert set(metrics) == {
        "tiny.cards.recall@10",
        "tiny.cards.mrr@10",
        "tiny.rules.recall@10",
        "tiny.rules.mrr@10",
    }
    assert metrics["tiny.cards.recall@10"] == 1.0


def test_a_degraded_search_fails_the_gate(
    loaded: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    with connect(loaded) as conn:
        baseline = retrieval_metrics(conn, FakeEmbedder(), SET)
    thresholds = {name: value - 0.02 for name, value in baseline.items()}

    # A broken fusion: the best match comes last.
    real = search.reciprocal_rank_fusion
    monkeypatch.setattr(
        search, "reciprocal_rank_fusion", lambda *a, **kw: list(reversed(real(*a, **kw)))
    )
    with connect(loaded) as conn:
        degraded = retrieval_metrics(conn, FakeEmbedder(), SET)

    assert check(baseline, thresholds) == []
    assert any(failure.startswith("tiny.cards.mrr@10") for failure in check(degraded, thresholds))


def test_the_validator_must_find_exactly_the_labelled_violations(loaded: Settings) -> None:
    legal = {card["name"]: card["count"] for card in LEGAL_CARDS}
    short = dict(legal, **{RATS: 59})
    cases = [
        ValidatorCase(id="V1", description="legal", commander=ATRAXA, cards=legal, expected=[]),
        ValidatorCase(
            id="V2", description="99 cards", commander=ATRAXA, cards=short, expected=["deck_size"]
        ),
        ValidatorCase(
            id="V3",
            description="mislabelled: says legal, but has 99 cards",
            commander=ATRAXA,
            cards=short,
            expected=[],
        ),
    ]

    with connect(loaded) as conn:
        metrics, failures = validator_metrics(conn, cases)

    assert metrics == {"validator.accuracy": pytest.approx(2 / 3)}
    assert failures == ["V3: expected no violations, found deck_size"]
    assert COHORT in legal  # a card with a hyphen and an accent resolves by name
