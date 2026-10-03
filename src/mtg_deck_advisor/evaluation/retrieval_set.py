"""The labelled retrieval eval set (EVL-1): card queries and rules questions.

Card queries come in two scopes. "catalogue" queries search every
Commander-legal card and have a few known targets each. "pool" queries search
a fixed 300-card pool, and every card in it was judged, so their relevant
lists are complete and recall is exact. Rules questions list the rule numbers
that answer them.

There are two sets. **dev** (the first, written before any search code) is
what retrieval is tuned on. **test** is held out: written before any tuning,
frozen, and scored only for a baseline and for the final configuration, so the
reported gain isn't flattered by tuning on the same queries (#71).

`python -m mtg_deck_advisor.evaluation.retrieval_set [--set dev|test]` checks
a set against the database, and the test set against the dev set.
"""

import argparse
import csv
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Literal
from uuid import UUID

import psycopg
from pydantic import BaseModel, ConfigDict

from mtg_deck_advisor.ingestion.pool import parse_text
from mtg_deck_advisor.retrieval.search import CardFilters

# The repository's evals/datasets, wherever the command is run from.
DATASETS = Path(__file__).parents[3] / "evals" / "datasets"
CARD_QUERIES = DATASETS / "retrieval_cards.csv"
RULE_QUESTIONS = DATASETS / "retrieval_rules.csv"
POOL = DATASETS / "pool_300.txt"
SET_FILES = {
    "dev": (CARD_QUERIES, RULE_QUESTIONS, POOL),
    "test": (
        DATASETS / "retrieval_cards_test.csv",
        DATASETS / "retrieval_rules_test.csv",
        DATASETS / "pool_300_test.txt",
    ),
}
type SetName = Literal["dev", "test"]

# The cards the embedding model was chosen on (ADR 0009). A held-out set
# leaves them out, so the model choice can't flatter the test results.
MODEL_SELECTION_CARDS = frozenset(
    {
        "Sol Ring",
        "Lightning Bolt",
        "Wrath of God",
        "Counterspell",
        "Demonic Tutor",
        "Harmonize",
        "Animate Dead",
        "Rampant Growth",
        "Swords to Plowshares",
        "Glorious Anthem",
        "Raise the Alarm",
        "Lightning Greaves",
        "Craterhoof Behemoth",
        "Command Tower",
        "Control Magic",
        "Cultivate",
        "Eternal Witness",
        "Beast Within",
        "Rhystic Study",
        "Smothering Tithe",
    }
)

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


class EvalSet(BaseModel):
    name: str
    card_queries: list[CardQuery]
    rule_questions: list[RuleQuestion]
    pool: list[str]


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


def load_set(name: SetName) -> EvalSet:
    cards, rules, pool = SET_FILES[name]
    return EvalSet(
        name=name,
        card_queries=load_card_queries(cards),
        rule_questions=load_rule_questions(rules),
        pool=load_pool(pool),
    )


def search_filters(query: CardQuery, pool_ids: list[UUID]) -> CardFilters:
    """The search filters a query runs with: its own, plus the pool for pool queries."""
    return CardFilters(
        color_identity_within=query.filters.identity,
        types=query.filters.types,
        mana_value_min=query.filters.mv_min,
        mana_value_max=query.filters.mv_max,
        oracle_ids=pool_ids if query.scope == "pool" else None,
    )


def resolve_names(conn: psycopg.Connection, names: set[str]) -> dict[str, UUID]:
    """Oracle IDs for labelled card names (current, Commander-legal cards)."""
    rows = conn.execute(
        "SELECT name, oracle_id FROM cards WHERE removed_at IS NULL "
        "AND commander_legality = 'legal' AND name = ANY(%s)",
        (sorted(names),),
    ).fetchall()
    found: dict[str, UUID] = dict(rows)
    if missing := names - set(found):
        raise ValueError(f"labels name unknown cards: {sorted(missing)}")
    return found


def held_out_problems(dev: EvalSet, test: EvalSet) -> list[str]:
    """Every way the test set overlaps the dev set or the model selection; empty if none."""
    problems = [
        f"pool: {name} is also in the dev pool" for name in test.pool if name in set(dev.pool)
    ]
    dev_cards = {name for query in dev.card_queries for name in query.relevant}
    dev_rules = {number for question in dev.rule_questions for number in question.relevant}
    dev_texts = {query.query.casefold() for query in dev.card_queries} | {
        question.question.casefold() for question in dev.rule_questions
    }
    for query in test.card_queries:
        if query.query.casefold() in dev_texts:
            problems.append(f"{query.id}: {query.query!r} repeats a dev query")
        for name in query.relevant:
            if name in dev_cards:
                problems.append(f"{query.id}: {name} is labelled in the dev set")
            if name in MODEL_SELECTION_CARDS:
                problems.append(f"{query.id}: the embedding model was chosen on {name} (ADR 0009)")
    for question in test.rule_questions:
        if question.question.casefold() in dev_texts:
            problems.append(f"{question.id}: {question.question!r} repeats a dev question")
        problems += [
            f"{question.id}: rule {number} answers a dev question"
            for number in question.relevant
            if number in dev_rules
        ]
    return problems


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


def main(argv: Sequence[str] | None = None) -> int:
    from mtg_deck_advisor.config import get_settings
    from mtg_deck_advisor.db.connection import connect

    parser = argparse.ArgumentParser(prog="python -m mtg_deck_advisor.evaluation.retrieval_set")
    parser.add_argument("--set", choices=list(SET_FILES), default="dev", dest="name")
    eval_set = load_set(parser.parse_args(argv).name)
    with connect(get_settings()) as conn:
        problems = check_against_db(
            conn, eval_set.card_queries, eval_set.rule_questions, eval_set.pool
        )
    if eval_set.name == "test":
        problems += held_out_problems(load_set("dev"), eval_set)
    for problem in problems:
        print(problem)
    print(
        f"{eval_set.name}: {len(eval_set.card_queries)} card queries, "
        f"{len(eval_set.rule_questions)} rules questions, {len(eval_set.pool)} pool cards: "
        f"{len(problems)} problems"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
