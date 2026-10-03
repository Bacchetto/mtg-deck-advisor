"""Retrieval experiments: run variants on the dev set and compare them (#72).

    python scripts/retrieval_experiments.py list
    python scripts/retrieval_experiments.py run baseline
    python scripts/retrieval_experiments.py compare baseline <candidate>

A variant is a name plus how it searches cards and rules. `run` puts every
dev query through it and saves the per-query results under evals/runs/
(git-ignored). `compare` pairs two saved runs query by query and writes a
dated report under evals/reports/ that applies the adoption rule fixed in
`evaluation.retrieval` (+3 points, more wins than losses, no metric down more
than 2 points, median search within 2 s).

The held-out test set can't be run from here: it's scored only by
`evaluate_retrieval.py --set test`, for the baseline and the final.
"""

import argparse
import math
import statistics
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx2
import psycopg
from psycopg import sql
from pydantic import BaseModel, Field

from mtg_deck_advisor.config import get_settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.evaluation.retrieval import (
    Comparison,
    QueryResult,
    RunResult,
    adoption_decision,
    compare,
    score,
)
from mtg_deck_advisor.evaluation.retrieval_set import (
    CardQuery,
    EvalSet,
    RuleQuestion,
    load_set,
    resolve_names,
    search_filters,
)
from mtg_deck_advisor.llm.client import ModelClient
from mtg_deck_advisor.llm.errors import InvalidOutputError, ModelCallError, ModelError
from mtg_deck_advisor.llm.ollama import Embedder, OllamaEmbedder, OllamaProvider
from mtg_deck_advisor.llm.recording import MemoryRecorder
from mtg_deck_advisor.llm.types import Message, ModelRequest
from mtg_deck_advisor.observability.logging import configure_logging
from mtg_deck_advisor.retrieval.embeddings import vector_literal
from mtg_deck_advisor.retrieval.search import (
    CANDIDATES,
    CardFilters,
    SearchMode,
    card_conditions,
    search_cards,
    search_rules,
)
from mtg_deck_advisor.retrieval.text import card_text, query_text, rule_text

RUNS = Path("evals/runs")
REPORTS = Path("evals/reports/retrieval-experiments")
K = 10
SET_NAME = "dev"


@dataclass(frozen=True)
class Context:
    conn: psycopg.Connection
    embedder: Embedder


type CardSearch = Callable[[Context, CardQuery, CardFilters], list[str]]
type RuleSearch = Callable[[Context, RuleQuestion], list[str]]


@dataclass(frozen=True)
class Variant:
    name: str
    description: str
    cards: CardSearch
    rules: RuleSearch
    # Set when the variant searches another embedding model's vectors, which
    # must cover every card and rule before a run means anything.
    model: str | None = None


def cards_in_mode(mode: SearchMode) -> CardSearch:
    def search(ctx: Context, query: CardQuery, filters: CardFilters) -> list[str]:
        hits = search_cards(ctx.conn, ctx.embedder, query.query, filters, k=K, mode=mode)
        return [hit.name for hit in hits]

    return search


def rules_in_mode(mode: SearchMode) -> RuleSearch:
    def search(ctx: Context, question: RuleQuestion) -> list[str]:
        hits = search_rules(ctx.conn, ctx.embedder, question.question, k=K, mode=mode)
        return [hit.number for hit in hits]

    return search


# ------------------------------------------- query-side variants (#73)


class InstructionSwap:
    """The same embedder, with a different task instruction on queries.

    Queries reach the embedder as `Instruct: <instruction>\\nQuery: <text>`
    (retrieval.text.query_text). This swaps the instruction, or drops it
    (None), without touching production code; documents carry no
    instruction, so stored embeddings stay valid.
    """

    def __init__(self, inner: Embedder, instruction: str | None) -> None:
        self._inner = inner
        self._instruction = instruction

    @property
    def model(self) -> str:
        return self._inner.model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return self._inner.embed([self._swap(text) for text in texts])

    def _swap(self, text: str) -> str:
        if not text.startswith("Instruct: ") or "\nQuery: " not in text:
            return text
        query = text.split("\nQuery: ", 1)[1]
        return (
            query if self._instruction is None else f"Instruct: {self._instruction}\nQuery: {query}"
        )


def cards_with_instruction(instruction: str | None) -> CardSearch:
    def search(ctx: Context, query: CardQuery, filters: CardFilters) -> list[str]:
        embedder = InstructionSwap(ctx.embedder, instruction)
        hits = search_cards(ctx.conn, embedder, query.query, filters, k=K, mode="hybrid")
        return [hit.name for hit in hits]

    return search


def rules_with_instruction(instruction: str | None) -> RuleSearch:
    def search(ctx: Context, question: RuleQuestion) -> list[str]:
        embedder = InstructionSwap(ctx.embedder, instruction)
        hits = search_rules(ctx.conn, embedder, question.question, k=K, mode="vector")
        return [hit.number for hit in hits]

    return search


def weighted_rrf(rankings: Sequence[Sequence[str]], weights: Sequence[float]) -> list[str]:
    """Reciprocal rank fusion with a weight per ranking: sum of weight / (60 + rank)."""
    scores: dict[str, float] = {}
    for ranking, weight in zip(rankings, weights, strict=True):
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + weight / (60 + rank)
    return sorted(scores, key=lambda item: -scores[item])


def arm(
    ctx: Context, query: CardQuery, filters: CardFilters, mode: SearchMode, n: int
) -> list[str]:
    hits = search_cards(ctx.conn, ctx.embedder, query.query, filters, k=n, mode=mode)
    return [hit.name for hit in hits]


def all_words_first(ctx: Context, query: CardQuery, filters: CardFilters, n: int) -> list[str]:
    """Keyword arm: cards matching every word of the query first, then cards matching any."""
    conditions, params = card_conditions(filters)
    rows = ctx.conn.execute(
        sql.SQL(
            """
            SELECT c.name FROM cards c, (SELECT plainto_tsquery('english', %s) AS query) q
            WHERE c.search_vector @@ q.query AND {conditions}
            ORDER BY ts_rank_cd(c.search_vector, q.query, 1) DESC, c.name
            LIMIT %s
            """
        ).format(conditions=conditions),
        [query.query, *params, n],
    ).fetchall()
    every = [row[0] for row in rows]
    return (every + [name for name in arm(ctx, query, filters, "keyword", n) if name not in every])[
        :n
    ]


def cards_fused(
    depth: int = CANDIDATES, keyword_weight: float = 1.0, all_words: bool = False
) -> CardSearch:
    def search(ctx: Context, query: CardQuery, filters: CardFilters) -> list[str]:
        vector = arm(ctx, query, filters, "vector", depth)
        keyword = (
            all_words_first(ctx, query, filters, depth)
            if all_words
            else arm(ctx, query, filters, "keyword", depth)
        )
        return weighted_rrf([vector, keyword], [1.0, keyword_weight])[:K]

    return search


# ------------------------------------------ other embedding models (#73)

DIMENSIONS = 1024
EXPERIMENT_TABLE = "experiments.embeddings"


class TruncatingEmbedder:
    """An embedder whose vectors are cut to their first 1,024 dimensions and renormalised.

    Qwen3 embedding models are Matryoshka-trained: their leading dimensions
    form a valid smaller embedding. Truncating keeps the schema's
    vector(1024), and pgvector's HNSW index, which can't index more than
    2,000 dimensions, for models that produce up to 4,096.
    """

    def __init__(self, inner: Embedder, dimensions: int = DIMENSIONS) -> None:
        self._inner = inner
        self._dimensions = dimensions

    @property
    def model(self) -> str:
        return self._inner.model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = []
        for vector in self._inner.embed(texts):
            cut = vector[: self._dimensions]
            norm = math.sqrt(sum(x * x for x in cut))
            vectors.append([x / norm for x in cut])
        return vectors


def ollama_embedder(model: str) -> Embedder:
    settings = get_settings()
    inner = OllamaEmbedder(
        base_url=settings.ollama_base_url,
        model=model,
        timeout_seconds=settings.ollama_timeout_seconds,
    )
    return inner if model == settings.embedding_model else TruncatingEmbedder(inner)


def embed_with_retry(embedder: Embedder, texts: list[str], attempts: int = 5) -> list[list[float]]:
    """Embed, waiting out Windows running short of network ports.

    Ollama makes an internal HTTP call per text, and a long run at full speed
    can exhaust Windows' short-lived ports ("lacked sufficient buffer space").
    They free up within a minute or two, so wait and retry instead of failing.
    """
    for attempt in range(1, attempts + 1):
        try:
            return embedder.embed(texts)
        except ModelCallError as exc:
            if "buffer space" not in str(exc) or attempt == attempts:
                raise
            print(f"  out of network ports; waiting 90 s (attempt {attempt})", flush=True)
            time.sleep(90)
    raise RuntimeError("unreachable: the last attempt either returns or raises")


def unload_all() -> None:
    """Unload every Ollama model, so the next one has the GPU to itself (ADR 0009)."""
    base_url = get_settings().ollama_base_url
    for loaded in httpx2.get(f"{base_url}/api/ps", timeout=10).json().get("models", []):
        httpx2.post(
            f"{base_url}/api/generate", json={"model": loaded["name"], "keep_alive": 0}, timeout=60
        )


def vram_gb(model: str) -> float:
    base_url = get_settings().ollama_base_url
    running = httpx2.get(f"{base_url}/api/ps", timeout=10).json().get("models", [])
    return next((m["size_vram"] / 1e9 for m in running if m["name"] == model), 0.0)


def embed_with_model(model: str, limit: int | None) -> None:
    """Embed every legal card and rule with `model` into the experiments table (resumable)."""
    embedder = ollama_embedder(model)
    with connect(get_settings()) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS experiments")
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {EXPERIMENT_TABLE} (
                model text, kind text, key text, embedding vector({DIMENSIONS}) NOT NULL,
                PRIMARY KEY (model, kind, key)
            )
            """
        )
        conn.commit()
        done = {
            (row[0], row[1])
            for row in conn.execute(
                "SELECT kind, key FROM experiments.embeddings WHERE model = %s", (model,)
            ).fetchall()
        }
        cards = [
            ("card", str(row[0]), card_text(row[1], row[2], row[3]))
            for row in conn.execute(
                "SELECT oracle_id, name, type_line, oracle_text FROM cards "
                "WHERE removed_at IS NULL AND commander_legality = 'legal' ORDER BY name"
            ).fetchall()
        ]
        rules = [
            ("rule", row[0], rule_text(row[1]))
            for row in conn.execute(
                "SELECT number, text FROM rules WHERE removed_at IS NULL ORDER BY number"
            ).fetchall()
        ]
        everything = cards + rules
        conn.commit()  # end the reads, so each batch below commits on its own
        todo = [item for item in everything if (item[0], item[1]) not in done]
        if limit is not None:
            todo = todo[:limit]
        unload_all()
        embedder.embed(["warm-up"])
        started = time.perf_counter()
        for start in range(0, len(todo), 64):
            batch = todo[start : start + 64]
            vectors = embed_with_retry(embedder, [text for _, _, text in batch])
            with conn.transaction():
                conn.cursor().executemany(
                    "INSERT INTO experiments.embeddings VALUES (%s, %s, %s, %s::vector)",
                    [
                        (model, kind, key, vector_literal(vector))
                        for (kind, key, _), vector in zip(batch, vectors, strict=True)
                    ],
                )
            print(f"  {start + len(batch)}/{len(todo)}", end="\r", flush=True)
        seconds = time.perf_counter() - started
        rate = len(todo) / seconds if seconds else 0.0
        remaining = len(everything) - len(done) - len(todo)
        print(
            f"\n{model}: embedded {len(todo):,} in {seconds:.0f} s ({rate:.1f}/s), "
            f"VRAM {vram_gb(model):.1f} GB. Remaining {remaining:,}"
            + (f", about {remaining / rate / 60:.0f} min more." if rate and remaining else ".")
        )


def other_model_vector_arm(
    ctx: Context,
    model: str,
    text: str,
    kind: str,
    conditions: Any,
    params: list[Any],
    n: int,
    label: str | None = None,
) -> list[str]:
    """The vector arm over vectors stored under `label` (default: the model's name)."""
    label = label or model
    embedder = ollama_embedder(model)
    (vector,) = embedder.embed([query_text(model, "cards" if kind == "card" else "rules", text)])
    if kind == "rule":
        rows = ctx.conn.execute(
            "SELECT key FROM experiments.embeddings WHERE model = %s AND kind = 'rule' "
            "ORDER BY embedding <=> %s::vector LIMIT %s",
            (label, vector_literal(vector), n),
        ).fetchall()
        return [row[0] for row in rows]
    rows = ctx.conn.execute(
        sql.SQL(
            "SELECT c.name FROM experiments.embeddings e JOIN cards c ON c.oracle_id::text = e.key "
            "WHERE e.model = %s AND e.kind = 'card' AND {conditions} "
            "ORDER BY e.embedding <=> %s::vector LIMIT %s"
        ).format(conditions=conditions),
        [label, *params, vector_literal(vector), n],
    ).fetchall()
    return [row[0] for row in rows]


def cards_with_model(model: str, keyword_weight: float = 0.5) -> CardSearch:
    def search(ctx: Context, query: CardQuery, filters: CardFilters) -> list[str]:
        conditions, params = card_conditions(filters)
        vector = other_model_vector_arm(
            ctx, model, query.query, "card", conditions, params, CANDIDATES
        )
        keyword = arm(ctx, query, filters, "keyword", CANDIDATES)
        return weighted_rrf([vector, keyword], [1.0, keyword_weight])[:K]

    return search


def rules_with_model(model: str) -> RuleSearch:
    def search(ctx: Context, question: RuleQuestion) -> list[str]:
        return other_model_vector_arm(ctx, model, question.question, "rule", None, [], K)

    return search


# ------------------------------------------------ card summaries (#74)

SUMMARY_MODEL = "qwen3:14b"
SUMMARY_PROMPT_VERSION = "summaries-v1"
SUMMARY_BATCH = 25
BEST_EMBEDDER = "qwen3-embedding:8b"
SUMMARY_SYSTEM = (
    "You describe Magic: The Gathering cards for a search index. For each numbered card, "
    "write one plain-English sentence saying what the card does and what a Commander player "
    "would use it for, in the everyday words a player would search with (for example ramp, "
    "draws cards, destroys a creature, makes tokens, counters a spell). Don't repeat the "
    "card's name or copy its rules wording. Write 15 to 30 words."
)


class CardSummary(BaseModel):
    index: int
    summary: str = Field(min_length=10, max_length=300)


class CardSummaries(BaseModel):
    cards: list[CardSummary]


def summarise_batch(client: ModelClient, cards: Sequence[tuple[str, str, str, str]]) -> list[str]:
    """One sentence per card (name, type line, mana cost, rules text), in order."""
    listing = "\n".join(
        f"{number}. {name} | {mana_cost} | {type_line} | {text.replace(chr(10), ' / ')}"
        for number, (name, type_line, mana_cost, text) in enumerate(cards, start=1)
    )
    request = ModelRequest(
        purpose="card_summaries",
        system=SUMMARY_SYSTEM,
        messages=(Message(role="user", content=f"Describe these cards:\n{listing}"),),
        max_tokens=max(800, 90 * len(cards)),
        effort="low",
    )
    result = client.generate_structured(request, CardSummaries)
    by_index = {card.index: card.summary for card in result.value.cards}
    if sorted(by_index) != list(range(1, len(cards) + 1)):
        raise InvalidOutputError(f"summaries for {sorted(by_index)}, expected 1..{len(cards)}")
    return [by_index[i] for i in range(1, len(cards) + 1)]


def summarise_cards(names: Sequence[str] | None) -> None:
    """Summarise these cards (None: every legal card) with the local model, resumably."""
    settings = get_settings()
    provider = OllamaProvider(
        base_url=settings.ollama_base_url, timeout_seconds=settings.ollama_timeout_seconds
    )
    client = ModelClient(provider, SUMMARY_MODEL, recorder=MemoryRecorder())
    with connect(settings) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS experiments.card_summaries (
                oracle_id uuid, prompt_version text, model text, summary text NOT NULL,
                PRIMARY KEY (oracle_id, prompt_version, model)
            )
            """
        )
        conn.commit()
        rows = conn.execute(
            """
            SELECT c.oracle_id, c.name, c.type_line, c.mana_cost, c.oracle_text FROM cards c
            WHERE c.removed_at IS NULL AND c.commander_legality = 'legal'
              AND (%s::text[] IS NULL OR c.name = ANY(%s::text[]))
              AND NOT EXISTS (SELECT 1 FROM experiments.card_summaries s
                              WHERE s.oracle_id = c.oracle_id AND s.prompt_version = %s
                                AND s.model = %s)
            ORDER BY c.name
            """,
            (
                None if names is None else list(names),
                None if names is None else list(names),
                SUMMARY_PROMPT_VERSION,
                SUMMARY_MODEL,
            ),
        ).fetchall()
        total = conn.execute(
            "SELECT count(*) FROM cards WHERE removed_at IS NULL AND commander_legality = 'legal'"
        ).fetchone()
        conn.commit()
        unload_all()
        failed = 0
        started = time.perf_counter()
        for start in range(0, len(rows), SUMMARY_BATCH):
            batch = rows[start : start + SUMMARY_BATCH]
            try:
                summaries = summarise_batch(client, [row[1:] for row in batch])
            except ModelError as exc:
                # At temperature 0 a bad batch fails the same way every time (the
                # model skipped one of two vanilla cards), so retry card by card.
                print(f"\n  batch at {start} failed ({exc}); retrying its cards one by one")
                kept = []
                for row in batch:
                    try:
                        (summary,) = summarise_batch(client, [row[1:]])
                    except ModelError as card_exc:
                        failed += 1
                        print(f"  {row[1]}: {card_exc}")
                        continue
                    kept.append((row, summary))
                batch, summaries = [r for r, _ in kept], [s for _, s in kept]
            with conn.transaction():
                conn.cursor().executemany(
                    "INSERT INTO experiments.card_summaries VALUES (%s, %s, %s, %s)",
                    [
                        (row[0], SUMMARY_PROMPT_VERSION, SUMMARY_MODEL, summary)
                        for row, summary in zip(batch, summaries, strict=True)
                    ],
                )
            print(f"  {start + len(batch)}/{len(rows)}", end="\r", flush=True)
        seconds = time.perf_counter() - started
        per_card = seconds / max(1, len(rows) - failed)
        full = (total[0] if total else 0) * per_card
        print(
            f"\nsummarised {len(rows) - failed:,} cards in {seconds:.0f} s "
            f"({per_card:.2f} s per card), {failed} failed. The full catalogue would take "
            f"about {full / 3600:.1f} h."
        )


def embed_summaries() -> None:
    """Embed the summarised cards two ways, with the best embedding model."""
    embedder = ollama_embedder(BEST_EMBEDDER)
    with connect(get_settings()) as conn:
        rows = conn.execute(
            """
            SELECT c.oracle_id, c.name, c.type_line, c.oracle_text, s.summary
            FROM experiments.card_summaries s JOIN cards c USING (oracle_id)
            WHERE s.prompt_version = %s AND s.model = %s
              AND NOT EXISTS (SELECT 1 FROM experiments.embeddings e
                              WHERE e.model = %s AND e.kind = 'card'
                                AND e.key = s.oracle_id::text)
            """,
            (SUMMARY_PROMPT_VERSION, SUMMARY_MODEL, f"{BEST_EMBEDDER}|summary-only"),
        ).fetchall()
        conn.commit()
        unload_all()
        # Appending the summary lost in the pilot; only the summary vector is kept.
        variants = {f"{BEST_EMBEDDER}|summary-only": [(str(r[0]), r[4]) for r in rows]}
        for label, items in variants.items():
            for start in range(0, len(items), 64):
                batch = items[start : start + 64]
                vectors = embed_with_retry(embedder, [text for _, text in batch])
                with conn.transaction():
                    conn.cursor().executemany(
                        "INSERT INTO experiments.embeddings VALUES (%s, 'card', %s, %s::vector) "
                        "ON CONFLICT (model, kind, key) "
                        "DO UPDATE SET embedding = EXCLUDED.embedding",
                        [
                            (label, key, vector_literal(v))
                            for (key, _), v in zip(batch, vectors, strict=True)
                        ],
                    )
            print(f"embedded {len(items)} cards as {label}")


def cards_with_summaries(how: str, everywhere: bool = False) -> CardSearch:
    """Search with the summary vectors: for pool queries only, or for every query.

    In the pilot only pool cards were summarised, so catalogue searches would
    have pitted summarised cards against unsummarised ones; they fell back to
    the best config (compare with `--scope pool`). With every card summarised,
    `everywhere` uses the summaries for catalogue queries too.
    """
    best = cards_with_model(BEST_EMBEDDER)

    def search(ctx: Context, query: CardQuery, filters: CardFilters) -> list[str]:
        if query.scope != "pool" and not everywhere:
            return best(ctx, query, filters)
        conditions, params = card_conditions(filters)
        keyword = arm(ctx, query, filters, "keyword", CANDIDATES)

        def vectors(label: str) -> list[str]:
            return other_model_vector_arm(
                ctx, BEST_EMBEDDER, query.query, "card", conditions, params, CANDIDATES, label
            )

        if how == "appended":
            return weighted_rrf(
                [vectors(f"{BEST_EMBEDDER}|summary-appended"), keyword], [1.0, 0.5]
            )[:K]
        return weighted_rrf(
            [vectors(BEST_EMBEDDER), vectors(f"{BEST_EMBEDDER}|summary-only"), keyword],
            [1.0, 1.0, 0.5],
        )[:K]

    return search


# The instruction ADR 0009 measured, and alternatives. Many queries are
# deck-building needs or names, not descriptions of one card's effect.
CARD_INSTRUCTIONS = {
    "search": "Given a search for Magic: The Gathering cards by name, mechanic or effect, "
    "retrieve the cards that match",
    "needs": "Given what a Commander deck needs, retrieve Magic: The Gathering cards whose "
    "rules text provides it",
}
RULES_INSTRUCTION = (
    "Given a question about playing Magic: The Gathering, retrieve the Comprehensive Rules "
    "passage that answers it"
)

BASELINE_RULES = rules_in_mode("vector")

VARIANTS: dict[str, Variant] = {
    variant.name: variant
    for variant in [
        Variant(
            "baseline",
            "As shipped in #70: cards hybrid, rules vector.",
            cards_in_mode("hybrid"),
            BASELINE_RULES,
        ),
        Variant(
            "all-vector",
            "Vector search for cards and rules.",
            cards_in_mode("vector"),
            BASELINE_RULES,
        ),
        Variant(
            "instr-search",
            f"Card query instruction: {CARD_INSTRUCTIONS['search']!r}; rules: "
            f"{RULES_INSTRUCTION!r}.",
            cards_with_instruction(CARD_INSTRUCTIONS["search"]),
            rules_with_instruction(RULES_INSTRUCTION),
        ),
        Variant(
            "instr-needs",
            f"Card query instruction: {CARD_INSTRUCTIONS['needs']!r}.",
            cards_with_instruction(CARD_INSTRUCTIONS["needs"]),
            BASELINE_RULES,
        ),
        Variant(
            "instr-none",
            "No query instruction, for cards or rules.",
            cards_with_instruction(None),
            rules_with_instruction(None),
        ),
        Variant(
            "rrf-keyword-half",
            "Hybrid with the keyword arm's fusion weight halved (0.5).",
            cards_fused(keyword_weight=0.5),
            BASELINE_RULES,
        ),
        Variant(
            "depth-30",
            "Hybrid fusing each arm's top 30 (baseline: 50).",
            cards_fused(depth=30),
            BASELINE_RULES,
        ),
        Variant(
            "depth-100",
            "Hybrid fusing each arm's top 100 (baseline: 50).",
            cards_fused(depth=100),
            BASELINE_RULES,
        ),
        Variant(
            "fused-check",
            "The baseline rebuilt with this script's fusion (depth 50, equal weights): "
            "must match the baseline, to show the reimplementation is faithful.",
            cards_fused(),
            BASELINE_RULES,
        ),
        Variant(
            "keyword-half-depth-30",
            "The keyword arm at weight 0.5, fusing each arm's top 30.",
            cards_fused(depth=30, keyword_weight=0.5),
            BASELINE_RULES,
        ),
        Variant(
            "keyword-half-depth-100",
            "The keyword arm at weight 0.5, fusing each arm's top 100.",
            cards_fused(depth=100, keyword_weight=0.5),
            BASELINE_RULES,
        ),
        Variant(
            "embed-4b",
            "qwen3-embedding:4b truncated to 1,024 dimensions; cards hybrid with the keyword "
            "arm at 0.5 (the current best), rules vector.",
            cards_with_model("qwen3-embedding:4b"),
            rules_with_model("qwen3-embedding:4b"),
            model="qwen3-embedding:4b",
        ),
        Variant(
            "embed-8b",
            "qwen3-embedding:8b truncated to 1,024 dimensions; cards hybrid with the keyword "
            "arm at 0.5 (the current best), rules vector.",
            cards_with_model("qwen3-embedding:8b"),
            rules_with_model("qwen3-embedding:8b"),
            model="qwen3-embedding:8b",
        ),
        Variant(
            "embed-8b-keyword-full",
            "qwen3-embedding:8b, with the keyword arm at full weight (1.0).",
            cards_with_model("qwen3-embedding:8b", keyword_weight=1.0),
            rules_with_model("qwen3-embedding:8b"),
            model="qwen3-embedding:8b",
        ),
        Variant(
            "embed-8b-vector",
            "qwen3-embedding:8b, vector search only (no keyword arm).",
            cards_with_model("qwen3-embedding:8b", keyword_weight=0.0),
            rules_with_model("qwen3-embedding:8b"),
            model="qwen3-embedding:8b",
        ),
        Variant(
            "summary-appended",
            "Pilot: pool cards embedded (8b) with a one-sentence summary by qwen3:14b appended; "
            "catalogue queries unchanged from embed-8b.",
            cards_with_summaries("appended"),
            rules_with_model(BEST_EMBEDDER),
            model=BEST_EMBEDDER,
        ),
        Variant(
            "summary-vector",
            "Pilot: the summary embedded as its own vector and fused with the card vector and "
            "the keyword arm (weights 1, 1, 0.5); catalogue queries unchanged from embed-8b.",
            cards_with_summaries("vector"),
            rules_with_model(BEST_EMBEDDER),
            model=BEST_EMBEDDER,
        ),
        Variant(
            "summary-vector-all",
            "Every card summarised: the summary embedded as its own vector and fused with the "
            "card vector and the keyword arm (weights 1, 1, 0.5), for all card queries.",
            cards_with_summaries("vector", everywhere=True),
            rules_with_model(BEST_EMBEDDER),
            model=BEST_EMBEDDER,
        ),
        Variant(
            "keyword-all-words",
            "Hybrid whose keyword arm ranks cards matching every word first, then any word.",
            cards_fused(all_words=True),
            BASELINE_RULES,
        ),
    ]
}


# ------------------------------------------------------------------- run


def check_coverage(conn: psycopg.Connection, model: str) -> None:
    """Refuse to run on a partial embedding: missing cards silently look like misses."""
    row = conn.execute(
        """
        SELECT
            (SELECT count(*) FROM cards WHERE removed_at IS NULL AND commander_legality = 'legal')
            + (SELECT count(*) FROM rules WHERE removed_at IS NULL),
            (SELECT count(*) FROM experiments.embeddings WHERE model = %s)
        """,
        (model,),
    ).fetchone()
    expected, embedded = row if row is not None else (0, 0)
    if embedded < expected:
        raise SystemExit(
            f"{model} has {embedded:,} of {expected:,} cards and rules embedded: "
            f"finish with `embed {model}` first"
        )


def run(variant: Variant, eval_set: EvalSet, model: str) -> RunResult:
    settings = get_settings()
    embedder = OllamaEmbedder(
        base_url=settings.ollama_base_url,
        model=model,
        timeout_seconds=settings.ollama_timeout_seconds,
    )
    with connect(settings) as conn:
        if variant.model is not None:
            check_coverage(conn, variant.model)
        ctx = Context(conn, embedder)
        names = {name for query in eval_set.card_queries for name in query.relevant}
        ids = resolve_names(conn, names | set(eval_set.pool))
        pool_ids: list[UUID] = [ids[name] for name in eval_set.pool]

        # Warm up: load the model and the database caches before timing.
        first = eval_set.card_queries[0]
        variant.cards(ctx, first, search_filters(first, pool_ids))
        variant.rules(ctx, eval_set.rule_questions[0])

        cards: dict[str, QueryResult] = {}
        for query in eval_set.card_queries:
            started = time.perf_counter()
            ranked = variant.cards(ctx, query, search_filters(query, pool_ids))
            cards[query.id] = QueryResult(
                ranked=ranked, relevant=query.relevant, seconds=time.perf_counter() - started
            )
        rules: dict[str, QueryResult] = {}
        for question in eval_set.rule_questions:
            started = time.perf_counter()
            ranked = variant.rules(ctx, question)
            rules[question.id] = QueryResult(
                ranked=ranked, relevant=question.relevant, seconds=time.perf_counter() - started
            )
    return RunResult(
        variant=variant.name,
        set_name=eval_set.name,
        model=model,
        created=datetime.now().isoformat(timespec="seconds"),
        cards=cards,
        rules=rules,
    )


def run_path(name: str) -> Path:
    return RUNS / SET_NAME / f"{name}.json"


def latency(results: dict[str, QueryResult]) -> tuple[float, float]:
    """Median and 90th percentile seconds per search."""
    seconds = sorted(result.seconds for result in results.values())
    return statistics.median(seconds), seconds[int(0.9 * (len(seconds) - 1))]


def summarise(result: RunResult) -> str:
    lines = [f"{result.variant} on {result.set_name} ({result.model}):"]
    for label, results in (("cards", result.cards), ("rules", result.rules)):
        overall = score([(r.ranked, set(r.relevant)) for r in results.values()])
        median, p90 = latency(results)
        lines.append(
            f"  {label}: recall@10 {overall.recall_at_10:.2f}, MRR {overall.mrr:.2f}, "
            f"median {median * 1000:.0f} ms, p90 {p90 * 1000:.0f} ms"
        )
    return "\n".join(lines)


# --------------------------------------------------------------- compare


def interval(bounds: tuple[float, float]) -> str:
    return f"[{bounds[0] * 100:+.1f}, {bounds[1] * 100:+.1f}]"


def comparison_rows(
    label: str, comparison: Comparison, base: RunResult, cand: RunResult, kind: str
) -> list[str]:
    results = (base.cards, cand.cards) if kind == "cards" else (base.rules, cand.rules)
    b_score = score([(r.ranked, set(r.relevant)) for r in results[0].values()])
    c_score = score([(r.ranked, set(r.relevant)) for r in results[1].values()])
    return [
        f"| {label} | {comparison.queries} | {b_score.recall_at_10:.2f} → "
        f"{c_score.recall_at_10:.2f} ({comparison.recall_delta * 100:+.1f}, "
        f"95% {interval(comparison.recall_interval)}) | {b_score.mrr:.2f} → {c_score.mrr:.2f} "
        f"({comparison.mrr_delta * 100:+.1f}, 95% {interval(comparison.mrr_interval)}) | "
        f"{comparison.wins} / {comparison.losses} / {comparison.ties} |"
    ]


def group_rows(eval_set: EvalSet, base: RunResult, cand: RunResult) -> list[str]:
    groups: dict[str, list[str]] = {}
    for query in eval_set.card_queries:
        groups.setdefault(f"cards, {query.scope}", []).append(query.id)
        groups.setdefault(f"cards, `{query.kind}`", []).append(query.id)
    for question in eval_set.rule_questions:
        groups.setdefault(f"rules, `{question.kind}`", []).append(question.id)
    rows = []
    for label, ids in groups.items():
        source = (base.rules, cand.rules) if label.startswith("rules") else (base.cards, cand.cards)
        b = score([(source[0][i].ranked, set(source[0][i].relevant)) for i in ids])
        c = score([(source[1][i].ranked, set(source[1][i].relevant)) for i in ids])
        rows.append(
            f"| {label} ({len(ids)}) | {b.recall_at_10:.2f} → {c.recall_at_10:.2f} | "
            f"{b.mrr:.2f} → {c.mrr:.2f} |"
        )
    return rows


def changed_queries(eval_set: EvalSet, base: RunResult, cand: RunResult) -> list[str]:
    texts = {q.id: q.query for q in eval_set.card_queries} | {
        q.id: q.question for q in eval_set.rule_questions
    }
    rows = []
    for source in ((base.cards, cand.cards), (base.rules, cand.rules)):
        for query_id in sorted(source[0]):
            b, c = source[0][query_id], source[1][query_id]
            if (round(b.recall, 9), round(b.rr, 9)) == (round(c.recall, 9), round(c.rr, 9)):
                continue
            mark = "win" if (c.recall, c.rr) > (b.recall, b.rr) else "loss"
            rows.append(
                f"| {query_id} {texts[query_id]} | {mark} | {b.recall:.2f} → {c.recall:.2f} | "
                f"{b.rr:.2f} → {c.rr:.2f} |"
            )
    return rows


def compare_report(base: RunResult, cand: RunResult, eval_set: EvalSet) -> str:
    lines = [
        f"# Retrieval comparison: `{base.variant}` → `{cand.variant}`, {date.today().isoformat()}",
        "",
        f"Dev set ({len(eval_set.card_queries)} card queries, "
        f"{len(eval_set.rule_questions)} rules questions). Baseline: "
        f"{VARIANTS[base.variant].description if base.variant in VARIANTS else base.variant} "
        f"Candidate: "
        f"{VARIANTS[cand.variant].description if cand.variant in VARIANTS else cand.variant} "
        f"Script: `scripts/retrieval_experiments.py`.",
        "",
        "Deltas are in points (candidate minus baseline). The 95% intervals are bootstrap "
        "intervals over queries: with this few queries they're wide, and an interval that "
        "includes 0 means the change could be noise.",
        "",
        "| Set | queries | recall@10 | MRR@10 | wins / losses / ties |",
        "|---|---|---|---|---|",
    ]
    decisions = []
    for kind in ("cards", "rules"):
        b, c = (base.cards, cand.cards) if kind == "cards" else (base.rules, cand.rules)
        comparison = compare(b, c)
        lines += comparison_rows(kind.capitalize(), comparison, base, cand, kind)
        median, p90 = latency(c)
        decision = adoption_decision(comparison, median)
        decisions += [
            f"**{kind.capitalize()}: {'adopt' if decision.adopt else 'do not adopt'}.** "
            f"Candidate latency: median {median * 1000:.0f} ms, p90 {p90 * 1000:.0f} ms.",
            "",
            *[f"- {reason}" for reason in decision.reasons],
            "",
        ]
    lines += [
        "",
        "## Adoption rule",
        "",
        *decisions,
        "## By group (recall@10, MRR@10)",
        "",
        "| Group | recall@10 | MRR@10 |",
        "|---|---|---|",
        *group_rows(eval_set, base, cand),
        "",
        "## Queries that changed",
        "",
        "| Query | | recall@10 | reciprocal rank |",
        "|---|---|---|---|",
        *changed_queries(eval_set, base, cand),
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(prog="python scripts/retrieval_experiments.py")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list the variants")
    run_parser = commands.add_parser("run", help="run a variant on the dev set")
    run_parser.add_argument("variant", choices=list(VARIANTS))
    compare_parser = commands.add_parser("compare", help="compare two saved runs")
    compare_parser.add_argument("baseline")
    compare_parser.add_argument("candidate")
    embed_parser = commands.add_parser(
        "embed", help="embed every card and rule with another model (resumable)"
    )
    embed_parser.add_argument("model")
    embed_parser.add_argument("--limit", type=int, help="embed only this many, for timing")
    summarise_parser = commands.add_parser(
        "summarise", help="summarise the dev pool's cards with the local model"
    )
    summarise_parser.add_argument(
        "--all", action="store_true", help="every legal card, not just the dev pool (~7 h)"
    )
    commands.add_parser("embed-summaries", help="embed the summarised cards two ways")
    compare_parser.add_argument(
        "--scope", choices=["catalogue", "pool"], help="compare only card queries of this scope"
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings)
    if args.command == "embed":
        embed_with_model(args.model, args.limit)
        return 0
    if args.command == "summarise":
        summarise_cards(None if args.all else load_set(SET_NAME).pool)
        return 0
    if args.command == "embed-summaries":
        embed_summaries()
        return 0
    if args.command == "list":
        for variant in VARIANTS.values():
            print(f"{variant.name:24} {variant.description}")
        return 0
    eval_set = load_set(SET_NAME)
    if args.command == "run":
        result = run(VARIANTS[args.variant], eval_set, settings.embedding_model)
        result.save(run_path(result.variant))
        print(summarise(result))
        print(f"saved {run_path(result.variant)}")
        return 0
    base, cand = RunResult.load(run_path(args.baseline)), RunResult.load(run_path(args.candidate))
    if args.scope is not None:
        keep = {q.id for q in eval_set.card_queries if q.scope == args.scope}
        eval_set = eval_set.model_copy(
            update={"card_queries": [q for q in eval_set.card_queries if q.id in keep]}
        )
        base = base.model_copy(update={"cards": {i: r for i, r in base.cards.items() if i in keep}})
        cand = cand.model_copy(update={"cards": {i: r for i, r in cand.cards.items() if i in keep}})
    report = compare_report(base, cand, eval_set)
    REPORTS.mkdir(parents=True, exist_ok=True)
    scope = f"-{args.scope}" if args.scope else ""
    path = REPORTS / (
        f"{date.today().isoformat()}-compare-{args.baseline}-vs-{args.candidate}{scope}.md"
    )
    path.write_text(report, encoding="utf-8")
    print(report)
    print(f"saved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
