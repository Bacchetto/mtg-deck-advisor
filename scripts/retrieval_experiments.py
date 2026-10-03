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
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

import psycopg

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
from mtg_deck_advisor.llm.ollama import Embedder, OllamaEmbedder
from mtg_deck_advisor.observability.logging import configure_logging
from mtg_deck_advisor.retrieval.search import CardFilters, SearchMode, search_cards, search_rules

RUNS = Path("evals/runs")
REPORTS = Path("evals/reports")
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


VARIANTS: dict[str, Variant] = {
    variant.name: variant
    for variant in [
        Variant(
            "baseline",
            "As shipped in #70: cards hybrid, rules vector.",
            cards_in_mode("hybrid"),
            rules_in_mode("vector"),
        ),
        Variant(
            "all-vector",
            "Vector search for cards and rules.",
            cards_in_mode("vector"),
            rules_in_mode("vector"),
        ),
    ]
}


# ------------------------------------------------------------------- run


def run(variant: Variant, eval_set: EvalSet, model: str) -> RunResult:
    settings = get_settings()
    embedder = OllamaEmbedder(
        base_url=settings.ollama_base_url,
        model=model,
        timeout_seconds=settings.ollama_timeout_seconds,
    )
    with connect(settings) as conn:
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
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings)
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
    report = compare_report(base, cand, eval_set)
    path = REPORTS / f"{date.today().isoformat()}-compare-{args.baseline}-vs-{args.candidate}.md"
    path.write_text(report, encoding="utf-8")
    print(report)
    print(f"saved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
