"""Measure retrieval against the owner-reviewed eval set (EVL-1, EVL-6, RAG-5).

    python scripts/evaluate_retrieval.py                  # all modes, rule chunk variants
    python scripts/evaluate_retrieval.py --card-variants  # also card text with mana cost (~8 min)

Every labelled query (evals/datasets/retrieval_*.csv) runs through vector,
keyword and hybrid search, the same code the app uses, and is scored on
recall@5, recall@10 and MRR@10. Two embedding choices are also compared:

- rules: each rule's own text, as embedded, against the same text with its
  section and its parent's first sentence (ADR 0010's first design);
- cards (with --card-variants): the text ADR 0009 measured, against the same
  text plus the card's mana cost.

Variants are embedded into temporary tables, so the stored embeddings are
untouched. Everything is local and free. The report is saved, dated, under
evals/reports/; its Findings section is written by hand after reading it.
"""

import argparse
import re
import statistics
import sys
import time
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any, get_args
from uuid import UUID

import psycopg
from psycopg import sql

from mtg_deck_advisor.config import get_settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.evaluation.retrieval import RetrievalScore, recall_at_k, score
from mtg_deck_advisor.evaluation.retrieval_set import (
    CardQuery,
    RuleQuestion,
    load_card_queries,
    load_pool,
    load_rule_questions,
)
from mtg_deck_advisor.llm.ollama import Embedder, OllamaEmbedder
from mtg_deck_advisor.observability.logging import configure_logging
from mtg_deck_advisor.retrieval.embeddings import vector_literal
from mtg_deck_advisor.retrieval.fusion import reciprocal_rank_fusion
from mtg_deck_advisor.retrieval.search import (
    CANDIDATES,
    CardFilters,
    SearchMode,
    card_conditions,
    search_cards,
    search_rules,
)
from mtg_deck_advisor.retrieval.text import card_text, expand_symbols, query_text

REPORTS = Path("evals/reports")
MODES: tuple[SearchMode, ...] = get_args(SearchMode.__value__)
K = 10


class Run:
    """One configuration's results: per query, the ranked items and relevant items."""

    def __init__(self) -> None:
        self.results: dict[str, tuple[list[Any], set[Any]]] = {}
        self.seconds: list[float] = []

    def add(self, query_id: str, ranked: list[Any], relevant: set[Any], seconds: float) -> None:
        self.results[query_id] = (ranked, relevant)
        self.seconds.append(seconds)

    def score(self, ids: Sequence[str]) -> RetrievalScore:
        return score([self.results[query_id] for query_id in ids])

    def recall(self, query_id: str) -> float:
        ranked, relevant = self.results[query_id]
        return recall_at_k(ranked, relevant, K)


def card_filters(query: CardQuery, pool_ids: list[UUID]) -> CardFilters:
    return CardFilters(
        color_identity_within=query.filters.identity,
        types=query.filters.types,
        mana_value_min=query.filters.mv_min,
        mana_value_max=query.filters.mv_max,
        oracle_ids=pool_ids if query.scope == "pool" else None,
    )


def resolve_names(conn: psycopg.Connection, names: set[str]) -> dict[str, UUID]:
    rows = conn.execute(
        "SELECT name, oracle_id FROM cards WHERE removed_at IS NULL "
        "AND commander_legality = 'legal' AND name = ANY(%s)",
        (sorted(names),),
    ).fetchall()
    found = dict(rows)
    if missing := names - set(found):
        raise SystemExit(f"labels name unknown cards: {sorted(missing)}")
    return found


# ---------------------------------------------------------------- variants


def card_text_with_cost(name: str, type_line: str, mana_cost: str, oracle_text: str) -> str:
    """The card text variant: ADR 0009's text, plus each face's mana cost as words."""
    costs = [expand_symbols(cost) for cost in mana_cost.split("//") if cost.strip()]
    base = card_text(name, type_line, oracle_text)
    if not costs:
        return base
    head, _, rest = base.partition(f"{type_line}.")
    return f"{head}{type_line}. Costs {' or '.join(costs)}.{rest}"


SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def rule_text_with_context(number: str, section: str, text: str, parent: str | None) -> str:
    """The rule text variant ADR 0010 started with: section, parent's first sentence, rule."""
    context = ""
    if parent is not None:
        parent_number, parent_text = parent.split("\t", 1)
        first = SENTENCE_END.split(" ".join(parent_text.split()), maxsplit=1)[0]
        context = f" {parent_number}: {first}"
    return f"{section}.{context} {number}: {' '.join(text.split())}"


def embed_into_temp_table(
    conn: psycopg.Connection,
    embedder: Embedder,
    table: str,
    key_column: str,
    items: Sequence[tuple[Any, str]],
) -> None:
    names = {"table": sql.Identifier(table), "key": sql.Identifier(key_column)}
    key_type = sql.SQL("uuid" if key_column == "oracle_id" else "text")
    conn.execute(
        sql.SQL(
            "CREATE TEMP TABLE {table} ({key} {type} PRIMARY KEY, embedding vector(1024))"
        ).format(type=key_type, **names)
    )
    insert = sql.SQL("INSERT INTO {table} VALUES (%s, %s::vector)").format(**names)
    for start in range(0, len(items), 64):
        batch = items[start : start + 64]
        vectors = embedder.embed([text for _, text in batch])
        conn.cursor().executemany(
            insert, [(key, vector_literal(v)) for (key, _), v in zip(batch, vectors, strict=True)]
        )
        print(f"  {table}: {start + len(batch)}/{len(items)}", end="\r", flush=True)
    print()


def variant_card_ranking(
    conn: psycopg.Connection,
    embedder: Embedder,
    query: CardQuery,
    filters: CardFilters,
    limit: int,
) -> list[UUID]:
    conditions, params = card_conditions(filters)
    (vector,) = embedder.embed([query_text(embedder.model, "cards", query.query)])
    rows = conn.execute(
        sql.SQL(
            "SELECT e.oracle_id FROM card_variant e JOIN cards c USING (oracle_id) "
            "WHERE {conditions} ORDER BY e.embedding <=> %s::vector LIMIT %s"
        ).format(conditions=conditions),
        [*params, vector_literal(vector), limit],
    ).fetchall()
    return [row[0] for row in rows]


def variant_rule_ranking(
    conn: psycopg.Connection, embedder: Embedder, question: str, limit: int
) -> list[str]:
    (vector,) = embedder.embed([query_text(embedder.model, "rules", question)])
    rows = conn.execute(
        "SELECT number FROM rule_variant ORDER BY embedding <=> %s::vector LIMIT %s",
        (vector_literal(vector), limit),
    ).fetchall()
    return [row[0] for row in rows]


# ---------------------------------------------------------------- report


def fmt(value: float) -> str:
    return f"{value:.2f}"


def score_row(label: str, result: RetrievalScore) -> str:
    return (
        f"| {label} | {result.queries} | {fmt(result.recall_at_5)} | "
        f"{fmt(result.recall_at_10)} | {fmt(result.mrr)} |"
    )


def median_ms(run: Run) -> str:
    return f"{statistics.median(run.seconds) * 1000:.0f}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--card-variants",
        action="store_true",
        help="also compare card text with the mana cost (embeds every card again, ~8 min)",
    )
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings)
    embedder = OllamaEmbedder(
        base_url=settings.ollama_base_url,
        model=settings.embedding_model,
        timeout_seconds=settings.ollama_timeout_seconds,
    )
    card_queries = load_card_queries()
    rule_questions = load_rule_questions()
    pool_names = load_pool()
    embedder.embed(["warm-up"])  # load the model before timing anything

    with connect(settings) as conn:
        ids = resolve_names(
            conn, {name for q in card_queries for name in q.relevant} | set(pool_names)
        )
        pool_ids = [ids[name] for name in pool_names]

        card_runs = {mode: Run() for mode in MODES}
        for query in card_queries:
            relevant = {ids[name] for name in query.relevant}
            filters = card_filters(query, pool_ids)
            for mode in MODES:
                started = time.perf_counter()
                hits = search_cards(conn, embedder, query.query, filters, k=K, mode=mode)
                seconds = time.perf_counter() - started
                card_runs[mode].add(query.id, [h.oracle_id for h in hits], relevant, seconds)
        print(f"cards: {len(card_queries)} queries x {len(MODES)} modes")

        rule_runs = {mode: Run() for mode in MODES}
        keyword_rules: dict[str, list[str]] = {}
        for question in rule_questions:
            relevant = set(question.relevant)
            for mode in MODES:
                started = time.perf_counter()
                found = search_rules(conn, embedder, question.question, k=K, mode=mode)
                seconds = time.perf_counter() - started
                rule_runs[mode].add(question.id, [h.number for h in found], relevant, seconds)
            keyword_rules[question.id] = [
                h.number
                for h in search_rules(
                    conn, embedder, question.question, k=CANDIDATES, mode="keyword"
                )
            ]
        print(f"rules: {len(rule_questions)} questions x {len(MODES)} modes")

        # Rule chunk variant: with the section and the parent's first sentence.
        rows = conn.execute(
            """
            SELECT r.number, r.section, r.text, p.number || E'\t' || p.text
            FROM rules r LEFT JOIN rules p ON p.number = r.parent AND p.removed_at IS NULL
            WHERE r.removed_at IS NULL
            """
        ).fetchall()
        embed_into_temp_table(
            conn,
            embedder,
            "rule_variant",
            "number",
            [(r[0], rule_text_with_context(*r)) for r in rows],
        )
        rules_context = {"vector": Run(), "hybrid": Run()}
        for question in rule_questions:
            relevant = set(question.relevant)
            ranked = variant_rule_ranking(conn, embedder, question.question, CANDIDATES)
            rules_context["vector"].add(question.id, ranked[:K], relevant, 0)
            fused = reciprocal_rank_fusion([ranked, keyword_rules[question.id]])
            rules_context["hybrid"].add(question.id, [n for n, _ in fused[:K]], relevant, 0)

        cards_with_cost: dict[str, Run] = {}
        if args.card_variants:
            rows = conn.execute(
                "SELECT oracle_id, name, type_line, mana_cost, oracle_text FROM cards "
                "WHERE removed_at IS NULL AND commander_legality = 'legal'"
            ).fetchall()
            embed_into_temp_table(
                conn,
                embedder,
                "card_variant",
                "oracle_id",
                [(r[0], card_text_with_cost(*r[1:])) for r in rows],
            )
            cards_with_cost = {"vector": Run(), "hybrid": Run()}
            for query in card_queries:
                relevant = {ids[name] for name in query.relevant}
                filters = card_filters(query, pool_ids)
                ranked = variant_card_ranking(conn, embedder, query, filters, CANDIDATES)
                cards_with_cost["vector"].add(query.id, ranked[:K], relevant, 0)
                keyword = [
                    h.oracle_id
                    for h in search_cards(
                        conn, embedder, query.query, filters, k=CANDIDATES, mode="keyword"
                    )
                ]
                fused = reciprocal_rank_fusion([ranked, keyword])
                cards_with_cost["hybrid"].add(query.id, [i for i, _ in fused[:K]], relevant, 0)
        conn.rollback()  # drop the temporary tables; nothing else was written

    names_by_id = {oracle_id: name for name, oracle_id in ids.items()}
    report = render(
        settings.embedding_model,
        card_queries,
        rule_questions,
        card_runs,
        rule_runs,
        rules_context,
        cards_with_cost,
        names_by_id,
    )
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"{date.today().isoformat()}-retrieval.md"
    path.write_text(report, encoding="utf-8")
    print(report)
    print(f"saved {path}")
    return 0


def render(
    model: str,
    card_queries: list[CardQuery],
    rule_questions: list[RuleQuestion],
    card_runs: dict[SearchMode, Run],
    rule_runs: dict[SearchMode, Run],
    rules_context: dict[str, Run],
    cards_with_cost: dict[str, Run],
    names_by_id: dict[UUID, str],
) -> str:
    catalogue = [q.id for q in card_queries if q.scope == "catalogue"]
    pool = [q.id for q in card_queries if q.scope == "pool"]
    all_cards = [q.id for q in card_queries]
    rules = [q.id for q in rule_questions]
    header = "| Set | queries | recall@5 | recall@10 | MRR@10 |\n|---|---|---|---|---|"

    lines = [
        f"# Retrieval eval, {date.today().isoformat()}",
        "",
        f"Eval set: `evals/datasets/retrieval_cards.csv` ({len(card_queries)} card queries) and "
        f"`evals/datasets/retrieval_rules.csv` ({len(rule_questions)} rules questions), "
        "owner-reviewed, written before the search code. Embedding model: "
        f"`{model}`. Script: `scripts/evaluate_retrieval.py`. Each search returns its top "
        f"{K}; MRR counts 0 when nothing relevant is in them.",
        "",
        "## Results by mode",
        "",
    ]
    for mode in MODES:
        lines += [
            f"**{mode}**",
            "",
            header,
            score_row("Cards: catalogue", card_runs[mode].score(catalogue)),
            score_row("Cards: in pool", card_runs[mode].score(pool)),
            score_row("Cards: all", card_runs[mode].score(all_cards)),
            score_row("Rules", rule_runs[mode].score(rules)),
            "",
        ]

    lines += [
        "## Side by side (recall@10 / MRR@10)",
        "",
        "| Set | " + " | ".join(MODES) + " |",
        "|---|" + "---|" * len(MODES),
    ]
    groups: list[tuple[str, dict[SearchMode, Run], list[str]]] = [
        ("Cards: catalogue", card_runs, catalogue),
        ("Cards: in pool", card_runs, pool),
        ("Cards: all", card_runs, all_cards),
        ("Rules", rule_runs, rules),
    ]
    for kind in sorted({q.kind for q in card_queries}):
        groups.append(
            (f"Cards, kind `{kind}`", card_runs, [q.id for q in card_queries if q.kind == kind])
        )
    for kind in sorted({q.kind for q in rule_questions}):
        groups.append(
            (f"Rules, kind `{kind}`", rule_runs, [q.id for q in rule_questions if q.kind == kind])
        )
    for label, runs, ids in groups:
        cells = []
        for mode in MODES:
            result = runs[mode].score(ids)
            cells.append(f"{fmt(result.recall_at_10)} / {fmt(result.mrr)}")
        lines.append(f"| {label} ({len(ids)}) | " + " | ".join(cells) + " |")

    lines += [
        "",
        "## Latency",
        "",
        "Median per search, including embedding the query with the local model.",
        "",
        "| | " + " | ".join(MODES) + " |",
        "|---|" + "---|" * len(MODES),
        "| Cards (ms) | " + " | ".join(median_ms(card_runs[m]) for m in MODES) + " |",
        "| Rules (ms) | " + " | ".join(median_ms(rule_runs[m]) for m in MODES) + " |",
        "",
        "## Embedding text variants",
        "",
        "Rules: the rule's own text (as embedded), against the same text with its section "
        "and its parent's first sentence (ADR 0010's first design).",
        "",
        header,
        score_row("Rules, text alone: vector", rule_runs["vector"].score(rules)),
        score_row("Rules, with context: vector", rules_context["vector"].score(rules)),
        score_row("Rules, text alone: hybrid", rule_runs["hybrid"].score(rules)),
        score_row("Rules, with context: hybrid", rules_context["hybrid"].score(rules)),
    ]
    if cards_with_cost:
        lines += [
            "",
            "Cards: ADR 0009's text (name, type, rules text), against the same text plus "
            "the mana cost.",
            "",
            header,
            score_row("Cards, measured text: vector", card_runs["vector"].score(all_cards)),
            score_row("Cards, with cost: vector", cards_with_cost["vector"].score(all_cards)),
            score_row("Cards, measured text: hybrid", card_runs["hybrid"].score(all_cards)),
            score_row("Cards, with cost: hybrid", cards_with_cost["hybrid"].score(all_cards)),
        ]

    lines += [
        "",
        "## Per query (recall@10)",
        "",
        "Misses are the relevant items hybrid search didn't return in its top 10.",
        "",
        "| Query | " + " | ".join(MODES) + " | missed by hybrid |",
        "|---|" + "---|" * (len(MODES) + 1),
    ]
    for query in card_queries:
        ranked, relevant = card_runs["hybrid"].results[query.id]
        missed = sorted(names_by_id[i] for i in relevant - set(ranked))
        cells = " | ".join(fmt(card_runs[m].recall(query.id)) for m in MODES)
        lines.append(f"| {query.id} {query.query} | {cells} | {'; '.join(missed)} |")
    for question in rule_questions:
        ranked, relevant = rule_runs["hybrid"].results[question.id]
        missed = sorted(relevant - set(ranked))
        cells = " | ".join(fmt(rule_runs[m].recall(question.id)) for m in MODES)
        lines.append(f"| {question.id} {question.question} | {cells} | {'; '.join(missed)} |")

    lines += [
        "",
        "## Caveats",
        "",
        "- **Small set.** 44 card queries and 45 rules questions: one query moves a "
        "set's recall by about 2 points, and a kind with 2 queries by 50. Differences of "
        "a few points are noise.",
        "- **Labels.** Drafted by Claude from checkable sources and reviewed by the owner, "
        "who made no corrections. Catalogue queries list known targets, not every "
        "relevant card in 32,000, so their recall can understate a search that finds "
        "other good answers. Pool queries were judged against all 300 pool cards.",
        "- **Overlap with model selection.** Several catalogue queries describe cards the "
        "embedding model was chosen on (ADR 0009: Sol Ring, Command Tower, Rhystic Study, "
        "Smothering Tithe, Cultivate, Swords to Plowshares, Lightning Greaves, Demonic "
        "Tutor, Wrath of God, Counterspell), in different words. The model choice may "
        "flatter vector search on those.",
        "- **Variant search path.** Variants are embedded into a temporary table and "
        "searched exactly; the shipped configuration goes through the app's real search "
        "path. If anything, that favours the variants.",
        "- **Selection on the test set.** The defaults below were chosen on this same set, "
        "so the shipped configuration's numbers are somewhat optimistic. There's no "
        "held-out set yet; any further tuning (fusion weights, keyword query form) needs "
        "one first.",
        "",
        "## Findings",
        "",
        "_Written by hand after reading the numbers above._",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
