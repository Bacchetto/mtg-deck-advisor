"""A committed snapshot of cards, rules and embeddings, so retrieval can be evaluated in CI (#119).

CI has no GPU, so it can't run the local embedding model (`qwen3-embedding:8b`).
The snapshot carries what search needs instead, computed locally:

- a fixed subset of cards, with each card's embedding and, where it has one,
  its summary's embedding (pool searches fuse that third arm);
- every current rule, with its embedding;
- the eval queries, embedded as search embeds them (`query_text`), so a
  `SnapshotEmbedder` can answer for the model.

Loaded into an empty, migrated database, it searches as its source did for
those cards: the same SQL, fusion and filters run on the same vectors, to
half precision. Vectors are stored as base64 of little-endian float16 to
keep the files small; that moves a cosine similarity by about 0.001, so
near-ties can swap places. Its scores are compared with the snapshot's own
baseline, never with reports made on the full database.

    python -m mtg_deck_advisor.evaluation.snapshot   # export from the local database (needs Ollama)
"""

import base64
import gzip
import json
import struct
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg import sql

from mtg_deck_advisor.llm.ollama import Embedder
from mtg_deck_advisor.retrieval.embeddings import vector_literal
from mtg_deck_advisor.retrieval.text import query_text

SNAPSHOT_DIR = Path("evals/snapshot")
type QueryKind = Literal["cards", "rules"]


class UnknownQueryError(KeyError):
    """A text the snapshot has no embedding for."""


class SnapshotEmbedder:
    """Answers for the embedding model with the snapshot's stored query vectors."""

    def __init__(self, model: str, vectors: Mapping[str, list[float]]) -> None:
        self._model = model
        self._vectors = dict(vectors)

    @property
    def model(self) -> str:
        return self._model

    @property
    def texts(self) -> list[str]:
        """Every text the snapshot has a vector for."""
        return list(self._vectors)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        missing = [text for text in texts if text not in self._vectors]
        if missing:
            raise UnknownQueryError(
                f"the snapshot has no embedding for {missing[0][-80:]!r}: a new or changed eval "
                "query needs its embedding, so export the snapshot again "
                "(python -m mtg_deck_advisor.evaluation.snapshot)"
            )
        return [self._vectors[text] for text in texts]


def encode_vector(vector: Sequence[float]) -> str:
    return base64.b64encode(struct.pack(f"<{len(vector)}e", *vector)).decode("ascii")


def decode_vector(encoded: str) -> list[float]:
    raw = base64.b64decode(encoded)
    return list(struct.unpack(f"<{len(raw) // 2}e", raw))


def _parse_vector(text: str) -> list[float]:
    """A pgvector value as Postgres prints it: "[0.1,0.2,...]"."""
    return [float(x) for x in text.strip("[]").split(",")]


def _columns(conn: psycopg.Connection, table: str) -> list[str]:
    """A table's stored (not generated) columns."""
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = %s AND is_generated = 'NEVER' "
        "ORDER BY ordinal_position",
        (table,),
    ).fetchall()
    return [row[0] for row in rows]


def _write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> int:
    count = 0
    # mtime=0: the same content always gives the same bytes, so an unchanged
    # export doesn't show up as a change in git.
    with path.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        for record in records:
            gz.write((json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode())
            count += 1
    return count


def _read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as lines:
        for line in lines:
            yield json.loads(line)


def _rows(
    conn: psycopg.Connection, table: str, where: sql.Composable, params: Sequence[Any]
) -> list[dict[str, Any]]:
    columns = _columns(conn, table)
    statement = sql.SQL(
        "SELECT to_jsonb(t) FROM (SELECT {columns} FROM {table} WHERE {where}) t"
    ).format(
        columns=sql.SQL(", ").join(map(sql.Identifier, columns)),
        table=sql.Identifier(table),
        where=where,
    )
    return [row[0] for row in conn.execute(statement, params)]


def _vectors(
    conn: psycopg.Connection, table: str, key: str, keys: Sequence[Any], model: str
) -> dict[str, dict[str, Any]]:
    """A table's embeddings by key (as text): the vector, encoded, and what produced it."""
    statement = sql.SQL(
        "SELECT {key}::text, embedding::text, content_hash, text_version FROM {table} "
        "WHERE model = %s AND {key} = ANY(%s)"
    ).format(key=sql.Identifier(key), table=sql.Identifier(table))
    return {
        row[0]: {
            "embedding": encode_vector(_parse_vector(row[1])),
            "content_hash": row[2],
            "text_version": row[3],
        }
        for row in conn.execute(statement, (model, list(keys)))
    }


def export_snapshot(
    conn: psycopg.Connection,
    embedder: Embedder,
    directory: Path,
    *,
    card_ids: Iterable[UUID],
    queries: Iterable[tuple[QueryKind, str]],
    summary_ids: Iterable[UUID] | None = None,
    selection: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write the snapshot: the cards, every current rule, their embeddings, and the queries'.

    Card and rule embeddings are read from the database (the embedder's own
    model); the queries are embedded now with `embedder`. Summary embeddings,
    which only pool searches use, are kept for `summary_ids` (default: every
    card). Returns the manifest.
    """
    model = embedder.model
    directory.mkdir(parents=True, exist_ok=True)
    ids = sorted({str(card) for card in card_ids})

    cards = _rows(
        conn, "cards", sql.SQL("oracle_id = ANY(%s::uuid[]) AND removed_at IS NULL"), [ids]
    )
    card_vectors = _vectors(conn, "card_embeddings", "oracle_id", ids, model)
    summary_keys = ids if summary_ids is None else sorted({str(card) for card in summary_ids})
    summary_vectors = _vectors(conn, "summary_embeddings", "oracle_id", summary_keys, model)
    for card in cards:
        card["_embedding"] = card_vectors.get(card["oracle_id"])
        card["_summary_embedding"] = summary_vectors.get(card["oracle_id"])
    cards.sort(key=lambda card: card["oracle_id"])

    rules = _rows(conn, "rules", sql.SQL("removed_at IS NULL"), [])
    rule_vectors = _vectors(conn, "rule_embeddings", "number", [r["number"] for r in rules], model)
    for rule in rules:
        rule["_embedding"] = rule_vectors.get(rule["number"])
    rules.sort(key=lambda rule: rule["number"])

    wanted = sorted({(kind, query) for kind, query in queries})
    texts = [query_text(model, kind, query) for kind, query in wanted]
    vectors = embedder.embed(texts) if texts else []
    query_records = [
        {"kind": kind, "query": query, "text": text, "embedding": encode_vector(vector)}
        for (kind, query), text, vector in zip(wanted, texts, vectors, strict=True)
    ]

    manifest = {
        "model": model,
        "dimensions": len(vectors[0]) if vectors else None,
        "exported": date.today().isoformat(),
        "counts": {
            "cards": _write_jsonl(directory / "cards.jsonl.gz", cards),
            "card_embeddings": sum(1 for card in cards if card["_embedding"]),
            "summary_embeddings": sum(1 for card in cards if card["_summary_embedding"]),
            "rules": _write_jsonl(directory / "rules.jsonl.gz", rules),
            "rule_embeddings": sum(1 for rule in rules if rule["_embedding"]),
            "queries": _write_jsonl(directory / "queries.jsonl.gz", query_records),
        },
        "selection": dict(selection or {}),
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def _insert_rows(conn: psycopg.Connection, table: str, rows: list[dict[str, Any]]) -> None:
    """Insert rows written by `_rows`, in one statement (so rules can reference their parents)."""
    if not rows:
        return
    columns = sql.SQL(", ").join(map(sql.Identifier, _columns(conn, table)))
    conn.execute(
        sql.SQL(
            "INSERT INTO {table} ({columns}) SELECT {columns} "
            "FROM jsonb_populate_recordset(NULL::{table}, %s::jsonb)"
        ).format(table=sql.Identifier(table), columns=columns),
        (json.dumps(rows),),
    )


def _insert_vectors(
    conn: psycopg.Connection,
    table: str,
    key: str,
    rows: list[tuple[str, dict[str, Any]]],
    model: str,
) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            sql.SQL(
                "INSERT INTO {table} ({key}, embedding, content_hash, model, text_version) "
                "VALUES (%s, %s::vector, %s, %s, %s)"
            ).format(table=sql.Identifier(table), key=sql.Identifier(key)),
            [
                (
                    value,
                    vector_literal(decode_vector(v["embedding"])),
                    v["content_hash"],
                    model,
                    v["text_version"],
                )
                for value, v in rows
            ],
        )


def load_snapshot(conn: psycopg.Connection, directory: Path = SNAPSHOT_DIR) -> SnapshotEmbedder:
    """Load the snapshot into an empty, migrated database; return an embedder for its queries."""
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    model: str = manifest["model"]
    cards = list(_read_jsonl(directory / "cards.jsonl.gz"))
    rules = list(_read_jsonl(directory / "rules.jsonl.gz"))
    with conn.transaction():
        _insert_rows(conn, "cards", [_stored(card) for card in cards])
        _insert_rows(conn, "rules", [_stored(rule) for rule in rules])
        _insert_vectors(
            conn,
            "card_embeddings",
            "oracle_id",
            [(c["oracle_id"], c["_embedding"]) for c in cards if c["_embedding"]],
            model,
        )
        _insert_vectors(
            conn,
            "summary_embeddings",
            "oracle_id",
            [(c["oracle_id"], c["_summary_embedding"]) for c in cards if c["_summary_embedding"]],
            model,
        )
        _insert_vectors(
            conn,
            "rule_embeddings",
            "number",
            [(r["number"], r["_embedding"]) for r in rules if r["_embedding"]],
            model,
        )
    queries = {
        record["text"]: decode_vector(record["embedding"])
        for record in _read_jsonl(directory / "queries.jsonl.gz")
    }
    return SnapshotEmbedder(model, queries)


def _stored(record: dict[str, Any]) -> dict[str, Any]:
    """A row without the snapshot's own fields (those starting "_")."""
    return {key: value for key, value in record.items() if not key.startswith("_")}


# --- exporting the committed snapshot ------------------------------------------------

# Random Commander-legal cards beyond the labelled and pool cards, so a
# catalogue search has plenty of wrong answers to rank below the right ones.
DISTRACTORS = 3000
# Changing this draws a different, equally random set of distractors.
DISTRACTOR_SALT = "snapshot-v1"


def resolve_card_names(conn: psycopg.Connection, names: Iterable[str]) -> set[UUID]:
    """Cards for names, resolved as a pool is (front faces, TCGplayer tags); all must match."""
    from mtg_deck_advisor.ingestion.pool import PoolEntry
    from mtg_deck_advisor.ingestion.resolve import resolve

    entries = [
        PoolEntry(name=name, quantity=1, line=i) for i, name in enumerate(sorted(set(names)), 1)
    ]
    resolved = resolve(conn, entries)
    missing = [u.entry.name for u in resolved.unknown] + [a.entry.name for a in resolved.ambiguous]
    if missing:
        raise ValueError(f"names that match no single card: {missing[:10]}")
    return {match.card.oracle_id for match in resolved.matched}


def select_cards(conn: psycopg.Connection, names: Iterable[str]) -> list[UUID]:
    """The snapshot's cards: those named (labels, pools, validator decks), plus the distractors.

    Names are resolved as a pool is, so a front face or a TCGplayer-tagged name
    finds its card. Distractors are the first DISTRACTORS Commander-legal
    cards ordered by a salted hash of their ID: random, but the same every time.
    """
    chosen = resolve_card_names(conn, names)
    rows = conn.execute(
        "SELECT oracle_id FROM cards WHERE removed_at IS NULL AND commander_legality = 'legal' "
        "AND NOT oracle_id = ANY(%s) ORDER BY md5(oracle_id::text || %s) LIMIT %s",
        (list(chosen), DISTRACTOR_SALT, DISTRACTORS),
    ).fetchall()
    return sorted(chosen | {row[0] for row in rows})


def main() -> int:
    """Export the snapshot from the local database, embedding the eval queries with Ollama."""
    from mtg_deck_advisor.config import get_settings
    from mtg_deck_advisor.db.connection import connect
    from mtg_deck_advisor.evaluation.gate import POOLS, load_validator_cases
    from mtg_deck_advisor.evaluation.retrieval_set import load_set
    from mtg_deck_advisor.ingestion.pool import parse_csv, parse_text
    from mtg_deck_advisor.llm.factory import build_embedder

    settings = get_settings()
    sets = [load_set("dev"), load_set("test")]
    names: set[str] = set()
    for eval_set in sets:
        names |= {name for q in eval_set.card_queries for name in q.relevant}
    pool_names: set[str] = set()
    for path in POOLS:
        text = path.read_text(encoding="utf-8")
        parsed = parse_csv(text) if path.suffix == ".csv" else parse_text(text)
        pool_names |= {entry.name for entry in parsed.entries}
    names |= pool_names
    for case in load_validator_cases():
        names |= {case.commander, *case.cards}
    queries: list[tuple[QueryKind, str]] = []
    for eval_set in sets:
        queries += [("cards", q.query) for q in eval_set.card_queries]
        queries += [("rules", q.question) for q in eval_set.rule_questions]

    with connect(settings) as conn:
        card_ids = select_cards(conn, names)
        manifest = export_snapshot(
            conn,
            build_embedder(settings),
            SNAPSHOT_DIR,
            card_ids=card_ids,
            queries=queries,
            summary_ids=resolve_card_names(conn, pool_names),
            selection={
                "named_cards": len(names),
                "distractors": DISTRACTORS,
                "distractor_salt": DISTRACTOR_SALT,
                "sets": [s.name for s in sets],
                "pools": [p.as_posix() for p in POOLS],
            },
        )
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
