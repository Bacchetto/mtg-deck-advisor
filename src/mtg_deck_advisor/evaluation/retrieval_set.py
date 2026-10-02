"""The labelled retrieval eval set (EVL-1): card queries and rules questions.

Card queries come in two scopes. "catalogue" queries search every
Commander-legal card and have a few known targets each. "pool" queries search
a fixed 300-card pool, and every card in it was judged, so their relevant
lists are complete and recall is exact. Rules questions list the rule numbers
that answer them.

The labels were written before the search code, so the search can't have been
tuned to them. `python -m mtg_deck_advisor.evaluation.retrieval_set` checks
them against the database.
"""

import csv
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import psycopg
from pydantic import BaseModel, ConfigDict

from mtg_deck_advisor.ingestion.pool import parse_text

# The repository's evals/datasets, wherever the command is run from.
DATASETS = Path(__file__).parents[3] / "evals" / "datasets"
CARD_QUERIES = DATASETS / "retrieval_cards.csv"
RULE_QUESTIONS = DATASETS / "retrieval_rules.csv"
POOL = DATASETS / "pool_300.txt"

CARD_KINDS = ("paraphrase", "term", "name", "need")
RULE_KINDS = ("commander", "keyword", "general")
RULE_NUMBER = re.compile(r"^\d{3}\.\d+[a-z]*$")
COLORS = "WUBRG"


class QueryFilters(BaseModel):
    """The exact filters a query runs with, written `identity=G; type=Artifact; mv_max=3`."""

    model_config = ConfigDict(frozen=True)

    # Color identity within these colors; "" means colorless only (written "C").
    identity: str | None = None
    # Words that must all appear in the type line.
    types: list[str] = []
    mv_min: int | None = None
    mv_max: int | None = None


class CardQuery(BaseModel):
    id: str
    kind: Literal["paraphrase", "term", "name", "need"]
    scope: Literal["catalogue", "pool"]
    query: str
    filters: QueryFilters
    # Card names, exactly as stored (full names for two-faced cards).
    relevant: list[str]
    notes: str


class RuleQuestion(BaseModel):
    id: str
    kind: Literal["commander", "keyword", "general"]
    question: str
    # Rule numbers, such as "903.5a".
    relevant: list[str]
    notes: str


def parse_filters(text: str) -> QueryFilters:
    values: dict[str, object] = {}
    types: list[str] = []
    for part in _split(text):
        key, _, value = (piece.strip() for piece in part.partition("="))
        if key == "identity":
            if value != "C" and (not value or any(color not in COLORS for color in value)):
                raise ValueError(f"identity must be letters from {COLORS} or C, not {value!r}")
            values["identity"] = "" if value == "C" else value
        elif key == "type" and value:
            types.append(value)
        elif key in ("mv_min", "mv_max") and value.isdigit():
            values[key] = int(value)
        else:
            raise ValueError(f"unknown filter {part!r}: use identity, type, mv_min or mv_max")
    return QueryFilters.model_validate({**values, "types": types})


def _split(text: str) -> list[str]:
    return [item.strip() for item in text.split(";") if item.strip()]


def _rows(path: Path) -> list[tuple[int, dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        # The line a row starts on, for error messages (a field can span lines).
        return [(reader.line_num, row) for row in reader]


def _check_common(line: int, row: dict[str, str], kinds: Sequence[str], seen: set[str]) -> None:
    if row["id"] in seen:
        raise ValueError(f"line {line}: id {row['id']} is used twice")
    seen.add(row["id"])
    if row["kind"] not in kinds:
        raise ValueError(f"line {line}: kind must be one of {', '.join(kinds)}")
    relevant = _split(row["relevant"])
    if not relevant:
        raise ValueError(f"line {line}: no relevant items")
    if len(set(relevant)) != len(relevant):
        raise ValueError(f"line {line}: an item is listed twice")


def load_card_queries(path: Path = CARD_QUERIES) -> list[CardQuery]:
    queries: list[CardQuery] = []
    seen: set[str] = set()
    for line, row in _rows(path):
        _check_common(line, row, CARD_KINDS, seen)
        if row["scope"] not in ("catalogue", "pool"):
            raise ValueError(f"line {line}: scope must be catalogue or pool")
        if not row["query"].strip():
            raise ValueError(f"line {line}: the query is empty")
        try:
            filters = parse_filters(row["filters"])
        except ValueError as exc:
            raise ValueError(f"line {line}: bad filter: {exc}") from exc
        queries.append(
            CardQuery.model_validate(
                {**row, "filters": filters, "relevant": _split(row["relevant"])}
            )
        )
    return queries


def load_rule_questions(path: Path = RULE_QUESTIONS) -> list[RuleQuestion]:
    questions: list[RuleQuestion] = []
    seen: set[str] = set()
    for line, row in _rows(path):
        _check_common(line, row, RULE_KINDS, seen)
        relevant = _split(row["relevant"])
        if bad := [number for number in relevant if not RULE_NUMBER.match(number)]:
            raise ValueError(f"line {line}: not a rule number: {', '.join(bad)}")
        questions.append(RuleQuestion.model_validate({**row, "relevant": relevant}))
    return questions


def load_pool(path: Path = POOL) -> list[str]:
    parsed = parse_text(path.read_text(encoding="utf-8"))
    if parsed.problems:
        raise ValueError(f"{path}: {parsed.problems[0].message}")
    return [entry.name for entry in parsed.entries]


def _filter_problem(
    filters: QueryFilters, identity: list[str], type_line: str, mana_value: float
) -> str | None:
    if filters.identity is not None and not set(identity) <= set(filters.identity):
        return f"identity {''.join(identity) or 'C'} is outside {filters.identity or 'C'}"
    for word in filters.types:
        if not re.search(rf"\b{re.escape(word)}\b", type_line):
            return f"type line {type_line!r} has no {word}"
    if filters.mv_min is not None and mana_value < filters.mv_min:
        return f"mana value {mana_value:g} is below {filters.mv_min}"
    if filters.mv_max is not None and mana_value > filters.mv_max:
        return f"mana value {mana_value:g} is above {filters.mv_max}"
    return None


def check_against_db(
    conn: psycopg.Connection,
    card_queries: Sequence[CardQuery],
    rule_questions: Sequence[RuleQuestion],
    pool: Sequence[str],
) -> list[str]:
    """Every way the labels disagree with the stored cards and rules; empty if none."""
    names = {name for query in card_queries for name in query.relevant} | set(pool)
    cards = {
        row[0]: row[1:]
        for row in conn.execute(
            """
            SELECT name, commander_legality, color_identity, type_line, cmc
            FROM cards WHERE removed_at IS NULL AND name = ANY(%s)
            """,
            (sorted(names),),
        ).fetchall()
    }
    numbers = {number for question in rule_questions for number in question.relevant}
    rules = {
        row[0]
        for row in conn.execute(
            "SELECT number FROM rules WHERE removed_at IS NULL AND number = ANY(%s)",
            (sorted(numbers),),
        ).fetchall()
    }

    problems = [f"pool: no card named {name!r}" for name in pool if name not in cards]
    in_pool = set(pool)
    for query in card_queries:
        for name in query.relevant:
            if name not in cards:
                problems.append(f"{query.id}: no card named {name!r}")
                continue
            legality, identity, type_line, mana_value = cards[name]
            if legality != "legal":
                problems.append(f"{query.id}: {name} is not Commander-legal ({legality})")
            if query.scope == "pool" and name not in in_pool:
                problems.append(f"{query.id}: {name} is not in the pool")
            if reason := _filter_problem(query.filters, identity, type_line, mana_value):
                problems.append(f"{query.id}: {name} fails the query's filters: {reason}")
    for question in rule_questions:
        problems += [
            f"{question.id}: no rule {number}"
            for number in question.relevant
            if number not in rules
        ]
    return problems


def main() -> int:
    from mtg_deck_advisor.config import get_settings
    from mtg_deck_advisor.db.connection import connect

    card_queries = load_card_queries()
    rule_questions = load_rule_questions()
    pool = load_pool()
    with connect(get_settings()) as conn:
        problems = check_against_db(conn, card_queries, rule_questions, pool)
    for problem in problems:
        print(problem)
    print(
        f"{len(card_queries)} card queries, {len(rule_questions)} rules questions, "
        f"{len(pool)} pool cards: {len(problems)} problems"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
