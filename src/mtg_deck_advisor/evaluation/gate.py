"""The free eval gate: retrieval, validator and pool resolution against thresholds (#119, EVL-5).

    python -m mtg_deck_advisor.evaluation.gate            # a throwaway Postgres, via Docker
    python -m mtg_deck_advisor.evaluation.gate --ollama   # also embed the queries for real

Every number here is deterministic: the snapshot (`snapshot.py`) supplies
the cards, rules and embeddings, so the real search code runs without the
local embedding model, and the same code always scores the same. CI runs it
on every pull request, and a metric below its threshold in
`evals/thresholds.toml` fails the build.

- **Retrieval:** every labelled card query and rules question (dev and test
  sets), searched the way the app searches by default: hybrid for cards, vector
  for rules. There's no reranker, which needs a local model. Recall@10 and
  MRR@10, as in the retrieval eval.
- **Validator:** labelled decks (`evals/datasets/validator_decks.jsonl`), each
  of which must get exactly its expected violation codes.
- **Resolution:** the committed pools must resolve completely.

With `--ollama` the queries are embedded by the real model too (it must be
running and pulled), and each query's vector must match the snapshot's: a
check that the snapshot still stands for the model.
"""

import argparse
import json
import math
import os
import sys
import tomllib
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from pydantic import BaseModel

from mtg_deck_advisor.deck.facts import load_card_facts
from mtg_deck_advisor.deck.state import DeckState
from mtg_deck_advisor.evaluation.retrieval import score
from mtg_deck_advisor.evaluation.retrieval_set import (
    EvalSet,
    load_set,
    resolve_names,
    search_filters,
)
from mtg_deck_advisor.evaluation.snapshot import SNAPSHOT_DIR, SnapshotEmbedder, load_snapshot
from mtg_deck_advisor.guardrails.commander import validate
from mtg_deck_advisor.ingestion.pool import PoolEntry, parse_csv, parse_text
from mtg_deck_advisor.ingestion.resolve import resolve
from mtg_deck_advisor.llm.ollama import Embedder
from mtg_deck_advisor.retrieval.search import search_cards, search_rules

THRESHOLDS = Path("evals/thresholds.toml")
VALIDATOR_DECKS = Path("evals/datasets/validator_decks.jsonl")
POOLS = (
    Path("evals/datasets/pool_300.txt"),
    Path("evals/datasets/pool_300_test.txt"),
    Path("evals/datasets/pool_tcgplayer_collection.csv"),
)
K = 10
# How close a live query embedding must be to the snapshot's (cosine).
MIN_QUERY_SIMILARITY = 0.99


class ValidatorCase(BaseModel):
    id: str
    description: str
    commander: str
    # Card names and counts, the commander not included.
    cards: dict[str, int]
    # The pool file it's checked against (under evals/datasets/), or None for
    # a pool of exactly the deck's own cards, so only the format's rules apply.
    pool: str | None = None
    expected: list[str]


def load_thresholds(path: Path = THRESHOLDS) -> dict[str, float]:
    """The thresholds, flattened to "section.sub.metric" names."""

    def flatten(table: Mapping[str, Any], prefix: str) -> Iterator[tuple[str, float]]:
        for key, value in table.items():
            name = f"{prefix}{key}"
            if isinstance(value, Mapping):
                yield from flatten(value, f"{name}.")
            else:
                yield name, float(value)

    return dict(flatten(tomllib.loads(path.read_text(encoding="utf-8")), ""))


def check(metrics: Mapping[str, float], thresholds: Mapping[str, float]) -> list[str]:
    """Every threshold not met: below it, or not measured at all."""
    failures = []
    for name, minimum in thresholds.items():
        if name not in metrics:
            failures.append(f"{name} was not measured")
        elif metrics[name] < minimum:
            failures.append(f"{name} is {metrics[name]:.3f}, below its threshold of {minimum:.3f}")
    return failures


def retrieval_metrics(
    conn: psycopg.Connection, embedder: Embedder, eval_set: EvalSet
) -> dict[str, float]:
    """Recall@10 and MRR@10 for the set's card queries and rules questions, as the app searches."""
    ids = resolve_names(
        conn, {name for q in eval_set.card_queries for name in q.relevant} | set(eval_set.pool)
    )
    pool_ids = [ids[name] for name in eval_set.pool]
    cards = [
        (
            [
                hit.oracle_id
                for hit in search_cards(conn, embedder, q.query, search_filters(q, pool_ids), k=K)
            ],
            {ids[name] for name in q.relevant},
        )
        for q in eval_set.card_queries
    ]
    rules = [
        ([hit.number for hit in search_rules(conn, embedder, q.question, k=K)], set(q.relevant))
        for q in eval_set.rule_questions
    ]
    metrics: dict[str, float] = {}
    for kind, results in (("cards", cards), ("rules", rules)):
        if results:
            result = score(results)
            metrics[f"{eval_set.name}.{kind}.recall@{K}"] = result.recall_at_10
            metrics[f"{eval_set.name}.{kind}.mrr@{K}"] = result.mrr
    return metrics


def _resolved(
    conn: psycopg.Connection, names: Mapping[str, int]
) -> tuple[dict[str, Any], list[str]]:
    """Oracle IDs for card names, and the names that matched nothing."""
    entries = [
        PoolEntry(name=name, quantity=count, line=i)
        for i, (name, count) in enumerate(names.items(), 1)
    ]
    result = resolve(conn, entries)
    ids = {m.entry.name: m.card.oracle_id for m in result.matched}
    missing = [u.entry.name for u in result.unknown] + [a.entry.name for a in result.ambiguous]
    return ids, missing


def _pool_counts(conn: psycopg.Connection, path: Path) -> dict[Any, int]:
    text = path.read_text(encoding="utf-8")
    parsed = parse_csv(text) if path.suffix == ".csv" else parse_text(text)
    counts: dict[Any, int] = {}
    for match in resolve(conn, parsed.entries).matched:
        counts[match.card.oracle_id] = counts.get(match.card.oracle_id, 0) + match.entry.quantity
    return counts


def validator_metrics(
    conn: psycopg.Connection,
    cases: Sequence[ValidatorCase],
    datasets: Path = Path("evals/datasets"),
) -> tuple[dict[str, float], list[str]]:
    """The share of labelled decks given exactly their expected violation codes, and the misses."""
    failures: list[str] = []
    for case in cases:
        ids, missing = _resolved(conn, {case.commander: 1, **case.cards})
        if missing:
            failures.append(f"{case.id}: unknown cards: {', '.join(missing)}")
            continue
        deck = DeckState.new(ids[case.commander], {ids[name]: n for name, n in case.cards.items()})
        if case.pool is None:
            pool = {deck.commander: 1, **dict(deck.cards)}
        else:
            pool = _pool_counts(conn, datasets / case.pool)
        facts = load_card_facts(conn, [deck.commander, *deck.cards])
        found = sorted({v.code for v in validate(deck, facts, pool).violations})
        if found != sorted(set(case.expected)):
            expected = ", ".join(sorted(set(case.expected))) or "no violations"
            failures.append(f"{case.id}: expected {expected}, found {', '.join(found) or 'none'}")
    accuracy = (len(cases) - len(failures)) / len(cases) if cases else 0.0
    return {"validator.accuracy": accuracy}, failures


def load_validator_cases(path: Path = VALIDATOR_DECKS) -> list[ValidatorCase]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ValidatorCase.model_validate_json(line) for line in lines if line.strip()]


def resolution_metrics(conn: psycopg.Connection, pools: Sequence[Path] = POOLS) -> dict[str, float]:
    """The share of each pool's entries that resolve to a card."""
    metrics = {}
    for path in pools:
        text = path.read_text(encoding="utf-8")
        parsed = parse_csv(text) if path.suffix == ".csv" else parse_text(text)
        matched = len(resolve(conn, parsed.entries).matched)
        metrics[f"resolution.{path.stem}"] = (
            matched / len(parsed.entries) if parsed.entries else 0.0
        )
    return metrics


def query_similarity(snapshot: SnapshotEmbedder, live: Embedder, texts: Sequence[str]) -> float:
    """The lowest cosine similarity between the snapshot's and the live model's query vectors."""

    def cosine(a: Sequence[float], b: Sequence[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))

    stored = snapshot.embed(texts)
    fresh = live.embed(texts)
    return min(cosine(a, b) for a, b in zip(stored, fresh, strict=True))


@contextmanager
def _database(url: str | None) -> Iterator[str]:
    """The database to load the snapshot into: the one given, or a throwaway container."""
    if url:
        yield url
        return
    from testcontainers.community.postgres import PostgresContainer  # a dev dependency

    with PostgresContainer("pgvector/pgvector:0.8.6-pg16-trixie", driver=None) as container:
        yield container.get_connection_url()


def report(
    metrics: Mapping[str, float], thresholds: Mapping[str, float], notes: Sequence[str]
) -> str:
    lines = ["| Metric | Value | Threshold | |", "|---|---|---|---|"]
    for name in sorted({*metrics, *thresholds}):
        value = f"{metrics[name]:.3f}" if name in metrics else "-"
        minimum = f"{thresholds[name]:.3f}" if name in thresholds else "-"
        ok = (
            ""
            if name not in thresholds
            else ("pass" if metrics.get(name, -1) >= thresholds[name] else "FAIL")
        )
        lines.append(f"| {name} | {value} | {minimum} | {ok} |")
    return "\n".join(["## Eval gate", "", *lines, "", *(f"- {note}" for note in notes)]) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    from mtg_deck_advisor.config import Settings
    from mtg_deck_advisor.db.connection import connect
    from mtg_deck_advisor.db.migrate import upgrade
    from mtg_deck_advisor.llm.factory import build_embedder

    parser = argparse.ArgumentParser(prog="python -m mtg_deck_advisor.evaluation.gate")
    parser.add_argument(
        "--database-url", help="an empty database to use (default: a throwaway container)"
    )
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT_DIR)
    parser.add_argument("--thresholds", type=Path, default=THRESHOLDS)
    parser.add_argument(
        "--ollama", action="store_true", help="also embed the queries with the real model"
    )
    parser.add_argument(
        "--baseline", action="store_true", help="print the metrics as thresholds TOML"
    )
    args = parser.parse_args(argv)

    thresholds = load_thresholds(args.thresholds) if args.thresholds.exists() else {}
    notes: list[str] = []
    with _database(args.database_url) as url:
        settings = Settings(_env_file=None, database_url=url)
        upgrade(settings)
        with connect(settings) as conn:
            snapshot = load_snapshot(conn, args.snapshot)
            embedder: Embedder = snapshot
            if args.ollama:
                live = build_embedder(settings)  # OLLAMA_BASE_URL etc. from the environment
                similarity = query_similarity(snapshot, live, snapshot.texts)
                notes.append(
                    f"live query embeddings: lowest similarity to the snapshot {similarity:.4f}"
                )
                thresholds["ollama.query_similarity"] = MIN_QUERY_SIMILARITY
                embedder = live
            metrics: dict[str, float] = {}
            for name in ("dev", "test"):
                metrics |= retrieval_metrics(conn, embedder, load_set(name))
            validator, misses = validator_metrics(conn, load_validator_cases())
            metrics |= validator
            notes += misses
            metrics |= resolution_metrics(conn)
            if args.ollama:
                metrics["ollama.query_similarity"] = similarity

    if args.baseline:
        print(json.dumps(metrics, indent=2, sort_keys=True))
    failures = check(metrics, thresholds)
    text = report(metrics, thresholds, notes + failures)
    print(text)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as out:
            out.write(text)
    if failures:
        print(f"eval gate FAILED: {len(failures)} metric(s) below threshold", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
