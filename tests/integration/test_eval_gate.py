"""The embedding snapshot and the free eval gate, against real Postgres (#119).

The snapshot carries cards, rules and their embeddings, plus the embeddings
of the eval queries, so CI can run the real search code without the local
embedding model. These tests use fixture cards and the hashing FakeEmbedder.
"""

# Fixtures imported from other test modules are named again as test parameters,
# which is how pytest injects them; ruff reads that as a redefinition.
# ruff: noqa: F811

import uuid
from collections.abc import Iterator, Sequence
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
from mtg_deck_advisor.evaluation.snapshot import (
    QueryKind,
    _read_jsonl,
    export_snapshot,
    load_snapshot,
)
from mtg_deck_advisor.llm.fake import FakeEmbedder
from mtg_deck_advisor.retrieval.fusion import reciprocal_rank_fusion
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


def queries(eval_set: EvalSet) -> list[tuple[QueryKind, str]]:
    return [("cards", q.query) for q in eval_set.card_queries] + [
        ("rules", q.question) for q in eval_set.rule_questions
    ]


def exported(loaded: Settings, directory: Path) -> None:
    with connect(loaded) as conn:
        card_ids = [row[0] for row in conn.execute("SELECT oracle_id FROM cards")]
        export_snapshot(conn, FakeEmbedder(), directory, card_ids=card_ids, queries=queries(SET))


def test_a_loaded_snapshot_searches_like_its_source_to_half_precision(
    loaded: Settings, second_url: str, tmp_path: Path
) -> None:
    exported(loaded, tmp_path)
    copy = Settings(_env_file=None, database_url=second_url)
    upgrade(copy)
    card_query, rule_question = SET.card_queries[0].query, SET.rule_questions[0].question

    with connect(copy) as conn:
        embedder = load_snapshot(conn, tmp_path)
        loaded_cards = search_cards(conn, embedder, card_query, mode="vector")
        loaded_top = search_cards(conn, embedder, card_query)[0].name
        loaded_rules = search_rules(conn, embedder, rule_question)
    with connect(loaded) as conn:
        source_cards = search_cards(conn, FakeEmbedder(), card_query, mode="vector")
        source_top = search_cards(conn, FakeEmbedder(), card_query)[0].name
        source_rules = search_rules(conn, FakeEmbedder(), rule_question)

    assert embedder.model == "fake-embedder"
    assert loaded_top == source_top == "Sol Ring"
    # The same scores, to half precision: only near-ties can swap places.
    for loaded_scores, source_scores in (
        ([h.score for h in loaded_cards], [h.score for h in source_cards]),
        ([h.score for h in loaded_rules], [h.score for h in source_rules]),
    ):
        assert len(loaded_scores) == len(source_scores)
        for a, b in zip(loaded_scores, source_scores, strict=True):
            assert abs(a - b) < 0.002


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
    monkeypatch.setattr(
        "mtg_deck_advisor.retrieval.search.reciprocal_rank_fusion",
        lambda *a, **kw: list(reversed(reciprocal_rank_fusion(*a, **kw))),
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


class ShiftedEmbedder(FakeEmbedder):
    """Gives slightly different vectors, as a model can on a later run."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[x + 0.01 for x in self._vector(text)] for text in texts]


def test_exporting_again_keeps_the_vectors_already_in_the_snapshot(
    loaded: Settings, tmp_path: Path
) -> None:
    # Re-embedding a query can move it slightly, and with it the gate's
    # baseline. Only queries the snapshot hasn't seen are embedded.
    exported(loaded, tmp_path)
    before = {r["text"]: r["embedding"] for r in _read_jsonl(tmp_path / "queries.jsonl.gz")}
    shifted = ShiftedEmbedder()

    with connect(loaded) as conn:
        card_ids = [row[0] for row in conn.execute("SELECT oracle_id FROM cards")]
        export_snapshot(
            conn,
            shifted,
            tmp_path,
            card_ids=card_ids,
            queries=[*queries(SET), ("rules", "A new one?")],
        )

    after = {r["text"]: r["embedding"] for r in _read_jsonl(tmp_path / "queries.jsonl.gz")}
    assert {text: after[text] for text in before} == before
    assert len(after) == len(before) + 1
    assert shifted.calls == [[text for text in after if text not in before]]
