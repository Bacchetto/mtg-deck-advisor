"""The deck-build task set: success, cost and turns, and rubric-graded quality (#122, EVL-3, EVL-4).

    python -m mtg_deck_advisor.evaluation.deck_tasks --variant sonnet   # asks before spending

Each task in `evals/datasets/deck_tasks.jsonl` runs through the real flows:

- **A draft:** the task's pool is imported, and the agent drafts from it
  with the task's request.
- **A refine:** the harness saves the task's starting deck as version 1
  (code, not an approval, which only the user can give) and the agent
  refines it.

A task succeeds when the run leaves a legal proposal. The deck that proposal
would make is then graded by Opus 5.5 against `evals/rubrics/deck_quality.md`:
1-5 on plan coherence, fit to the request, mana base, ramp, card draw and
interaction. The grader is given each card's mana cost, type line and text,
and the deck's counts, so it doesn't rely on remembering cards. Eval decks are
archived afterwards, so they stay out of the user's deck lists.
"""

import argparse
import re
import statistics
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from uuid import UUID

import psycopg
from pydantic import BaseModel, Field, model_validator

from mtg_deck_advisor.agent.flows import AgentServices, draft_deck, import_pool, refine_deck
from mtg_deck_advisor.deck.state import DeckState
from mtg_deck_advisor.deck.store import create_deck, save_version, set_archived
from mtg_deck_advisor.evaluation.criteria import CRITERIA as CRITERIA
from mtg_deck_advisor.evaluation.runner import (
    CaseResult,
    EvalRun,
    Variant,
    comparison_report,
    confirm_spend,
    merge_results,
    result_from_run,
    run_suite,
    save_report,
    variant_services,
)
from mtg_deck_advisor.guardrails.audit import record_audit
from mtg_deck_advisor.guardrails.proposals import deck_proposals
from mtg_deck_advisor.ingestion.pool import PoolEntry
from mtg_deck_advisor.ingestion.resolve import resolve
from mtg_deck_advisor.llm.client import ModelClient
from mtg_deck_advisor.llm.errors import InvalidOutputError
from mtg_deck_advisor.llm.types import Message, ModelRequest

DECK_TASKS = Path("evals/datasets/deck_tasks.jsonl")
DATASETS = Path("evals/datasets")
RUBRIC = Path("evals/rubrics/deck_quality.md")
GRADER_MODEL = "claude-opus-5-5"

VARIANTS = {
    "sonnet": Variant(name="sonnet", model="claude-sonnet-5-5"),
    "haiku": Variant(name="haiku", model="claude-haiku-4-5"),
}
# Expected cost per task, from the recorded live runs (a draft $0.12-0.13, a
# refine $0.065 on Sonnet 5.5), with room to spare; a grade reads a whole deck.
AGENT_COST = {
    ("claude-sonnet-5-5", "draft"): 0.15,
    ("claude-sonnet-5-5", "refine"): 0.08,
    ("claude-haiku-4-5", "draft"): 0.06,
    ("claude-haiku-4-5", "refine"): 0.03,
}
GRADING_COST = 0.07


class StartDeck(BaseModel):
    commander: str
    cards: dict[str, int]


# --- goals a refine's request sets, checked by code (#136) ---------------------------------
# Each compares the refined deck with the deck it started from. Mana values and
# roles are over nonland cards besides the commander, as analyze_deck counts them.


class LandsGoal(BaseModel):
    kind: Literal["lands"]
    equals: int

    def describe(self) -> str:
        return f"exactly {self.equals} lands"


class LandsChangeGoal(BaseModel):
    kind: Literal["lands_change"]
    equals: int

    def describe(self) -> str:
        return f"lands change by {self.equals:+d}"


class RoleChangeGoal(BaseModel):
    kind: Literal["role_change"]
    role: str
    at_least: int
    # Count only cards at or below this mana value ("cheap removal").
    max_mana_value: float | None = None

    def describe(self) -> str:
        cheap = f" at mana value {self.max_mana_value:g} or less" if self.max_mana_value else ""
        return f"{self.role}{cheap} up by {self.at_least} or more"


class TypeChangeGoal(BaseModel):
    kind: Literal["type_change"]
    type: str
    at_least: int

    def describe(self) -> str:
        return f"{self.type} cards up by {self.at_least} or more"


class TypeCutGoal(BaseModel):
    kind: Literal["type_cut"]
    type: str
    at_least: int

    def describe(self) -> str:
        return f"{self.at_least} or more {self.type} cards cut"


class NoneAtOrAboveGoal(BaseModel):
    kind: Literal["none_at_or_above"]
    mana_value: float

    def describe(self) -> str:
        return f"no nonland card at mana value {self.mana_value:g} or more"


class AverageManaValueLowerGoal(BaseModel):
    kind: Literal["average_mana_value_lower"]

    def describe(self) -> str:
        return "a lower average mana value"


Goal = Annotated[
    LandsGoal
    | LandsChangeGoal
    | RoleChangeGoal
    | TypeChangeGoal
    | TypeCutGoal
    | NoneAtOrAboveGoal
    | AverageManaValueLowerGoal,
    Field(discriminator="kind"),
]


class DeckTask(BaseModel):
    id: str
    # A pool file under evals/datasets/.
    pool: str
    kind: Literal["draft", "refine"]
    request: str
    # The commander the request names, if it names one.
    commander: str | None
    theme: str
    # For a refine: the deck it starts from.
    start: StartDeck | None
    notes: str
    # For a refine: what its request asks for, as goals code can check.
    goals: list[Goal] = []

    @model_validator(mode="after")
    def _starts(self) -> Self:
        if self.kind == "refine" and self.start is None:
            raise ValueError(f"{self.id}: a refine needs a starting deck")
        return self


def load_tasks(path: Path = DECK_TASKS) -> list[DeckTask]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [DeckTask.model_validate_json(line) for line in lines if line.strip()]


Score = Annotated[int, Field(ge=1, le=5)]


class CriterionScores(BaseModel):
    """One field per rubric criterion. Explicit fields, not a dict: structured
    output forbids undeclared keys, so an open dict could only come back empty."""

    plan: Score
    fit: Score | None = Field(description="Null when there was no request.")
    mana: Score
    ramp: Score
    draw: Score
    interaction: Score


class CriterionReasons(BaseModel):
    plan: str
    fit: str
    mana: str
    ramp: str
    draw: str
    interaction: str


class DeckGrade(BaseModel):
    scores: CriterionScores
    # One sentence per criterion, naming cards or counts.
    reasons: CriterionReasons
    summary: str

    @property
    def quality(self) -> float:
        given: list[int] = [s for s in self.scores.model_dump().values() if s is not None]
        return statistics.mean(given)


# --- checking a refine's goals ---------------------------------------------------------------


def _facts(
    conn: psycopg.Connection, ids: Sequence[UUID]
) -> dict[UUID, tuple[float, str, list[str]]]:
    """Each card's mana value, type line and roles."""
    rows = conn.execute(
        "SELECT c.oracle_id, c.cmc, c.type_line, coalesce(r.roles, '{}') FROM cards c "
        "LEFT JOIN card_roles r USING (oracle_id) WHERE c.oracle_id = ANY(%s)",
        (list(ids),),
    ).fetchall()
    return {row[0]: (float(row[1] or 0), row[2], list(row[3])) for row in rows}


def goal_results(
    conn: psycopg.Connection, goals: Sequence[Goal], start: DeckState, final: DeckState
) -> list[dict[str, Any]]:
    """Each goal, whether the refined deck meets it, and the value that decided it."""
    facts = _facts(conn, list({*start.cards, *final.cards}))

    def is_land(card: UUID) -> bool:
        return "Land" in facts[card][1].split("—")[0]

    def count(deck: DeckState, keep: Callable[[UUID], bool]) -> int:
        return sum(n for card, n in deck.cards.items() if keep(card))

    def average(deck: DeckState) -> float:
        values = [facts[c][0] for c, n in deck.cards.items() if not is_land(c) for _ in range(n)]
        return sum(values) / len(values) if values else 0.0

    results = []
    for goal in goals:
        if isinstance(goal, LandsGoal):
            actual = count(final, is_land)
            met, shown = actual == goal.equals, str(actual)
        elif isinstance(goal, LandsChangeGoal):
            actual = count(final, is_land) - count(start, is_land)
            met, shown = actual == goal.equals, f"{actual:+d}"
        elif isinstance(goal, RoleChangeGoal):

            def has_role(card: UUID, goal: RoleChangeGoal = goal) -> bool:
                cheap = goal.max_mana_value is None or facts[card][0] <= goal.max_mana_value
                return not is_land(card) and goal.role in facts[card][2] and cheap

            actual = count(final, has_role) - count(start, has_role)
            met, shown = actual >= goal.at_least, f"{actual:+d}"
        elif isinstance(goal, TypeChangeGoal):

            def typed(card: UUID, goal: TypeChangeGoal | TypeCutGoal = goal) -> bool:
                return goal.type in facts[card][1].split("—")[0]

            actual = count(final, typed) - count(start, typed)
            met, shown = actual >= goal.at_least, f"{actual:+d}"
        elif isinstance(goal, TypeCutGoal):
            cut = sum(
                max(start.count(card) - final.count(card), 0)
                for card in start.cards
                if goal.type in facts[card][1].split("—")[0]
            )
            met, shown = cut >= goal.at_least, str(cut)
        elif isinstance(goal, NoneAtOrAboveGoal):
            ceiling = goal.mana_value
            above = sum(
                n for c, n in final.cards.items() if not is_land(c) and facts[c][0] >= ceiling
            )
            met, shown = above == 0, f"{above} at or above"
        else:
            before, after = average(start), average(final)
            met, shown = after < before, f"{before:.2f} → {after:.2f}"
        results.append({"goal": goal.describe(), "met": met, "actual": shown})
    return results


def _start_state(conn: psycopg.Connection, start: StartDeck) -> DeckState:
    ids = _ids(conn, [start.commander, *start.cards])
    return DeckState.new(ids[start.commander], {ids[n]: c for n, c in start.cards.items()})


# --- what the grader sees --------------------------------------------------------------------


def deck_text(
    conn: psycopg.Connection, state: DeckState, request: str, start: DeckState | None = None
) -> str:
    """The deck as the grader reads it: the request, every card's text, and the counts.

    For a refine, `start` is the deck it began from. The text then ends with the
    change, so the grader can judge whether the change does what was asked
    (#136), not only how the final deck reads.
    """
    ids = [state.commander, *state.cards, *(start.cards if start else [])]
    rows = conn.execute(
        "SELECT oracle_id, name, mana_cost, cmc, type_line, oracle_text FROM cards "
        "WHERE oracle_id = ANY(%s)",
        (ids,),
    ).fetchall()
    cards = {row[0]: row[1:] for row in rows}

    def line(card: UUID, count: int) -> str:
        name, cost, _, type_line, text = cards[card]
        oracle = " ".join((text or "").split())
        return f"{count} {name} | {cost or '-'} | {type_line} | {oracle}"

    def counts(deck: DeckState) -> tuple[int, str]:
        lands = sum(n for card, n in deck.cards.items() if "Land" in cards[card][3])
        curve: dict[int, int] = {}
        for card, n in deck.cards.items():
            if "Land" not in cards[card][3]:
                value = min(int(cards[card][2] or 0), 7)
                curve[value] = curve.get(value, 0) + n
        text = ", ".join(f"{v if v < 7 else '7+'}: {curve[v]}" for v in sorted(curve))
        return lands, text

    lands, curve_text = counts(state)
    others = sorted(state.cards.items(), key=lambda item: cards[item[0]][0])
    lines = [
        f"Request: {request or 'none'}",
        "",
        f"Commander: {line(state.commander, 1)[2:]}",
        "",
        f"The other {state.total - 1} cards (count | name | mana cost | type | text):",
        *(line(card, n) for card, n in others),
        "",
        f"Lands: {lands}. Nonland cards by mana value: {curve_text}.",
    ]
    if start is not None:
        start_lands, start_curve = counts(start)

        def listed(changes: dict[UUID, int]) -> str:
            named = sorted((cards[card][0], n) for card, n in changes.items() if n > 0)
            return ", ".join(f"{n} {name}" for name, n in named) or "nothing"

        cut = {card: start.count(card) - state.count(card) for card in start.cards}
        added = {card: state.count(card) - start.count(card) for card in state.cards}
        lines += [
            "",
            "The change from the starting deck (this is a refine of a saved deck):",
            f"Lands: {start_lands} → {lands}.",
            f"Nonland cards by mana value, before: {start_curve}.",
            f"Cut: {listed(cut)}",
            f"Added: {listed(added)}",
        ]
    return "\n".join(lines)


def grade_deck(grader: ModelClient, text: str) -> DeckGrade:
    request = ModelRequest(
        purpose="deck_grading",
        system=(
            "You grade Commander decks, following this rubric exactly. Keep each reason to one "
            "sentence.\n\n" + RUBRIC.read_text(encoding="utf-8")
        ),
        messages=(Message(role="user", content=f"The deck to grade:\n<deck>\n{text}\n</deck>"),),
        max_tokens=2000,
    )
    return grader.generate_structured(request, DeckGrade).value


# --- running a task --------------------------------------------------------------------------


def _ids(conn: psycopg.Connection, names: Sequence[str]) -> dict[str, UUID]:
    entries = [PoolEntry(name=name, quantity=1, line=i) for i, name in enumerate(names, 1)]
    resolved = resolve(conn, entries)
    if missing := [u.entry.name for u in resolved.unknown] + [
        a.entry.name for a in resolved.ambiguous
    ]:
        raise ValueError(f"names that match no single card: {missing}")
    return {m.entry.name: m.card.oracle_id for m in resolved.matched}


def _pool(conn: psycopg.Connection, datasets: Path, name: str, pools: dict[str, UUID]) -> UUID:
    """The task's pool, imported once per suite run."""
    if name not in pools:
        text = (datasets / name).read_text(encoding="utf-8")
        source: Literal["text", "csv"] = "csv" if name.endswith(".csv") else "text"
        pools[name] = import_pool(conn, text, name=f"eval: {name}", source=source).pool_id
    return pools[name]


def run_task(
    services: AgentServices,
    grader: ModelClient,
    task: DeckTask,
    *,
    datasets: Path = DATASETS,
    pools: dict[str, UUID],
) -> CaseResult:
    """Run one task through the real flow, then grade the deck its legal proposal would make."""
    conn = services.conn
    pool_id = _pool(conn, datasets, task.pool, pools)
    details: dict[str, Any] = {"request": task.request, "kind": task.kind}
    start: DeckState | None = None
    if task.kind == "draft":
        drafted = draft_deck(services, pool_id, request=task.request, name=f"eval {task.id}")
        deck_id, run = drafted.deck_id, drafted.run
    else:
        if task.start is None:  # a refine always has one (DeckTask checks)
            raise ValueError(f"{task.id}: a refine needs a starting deck")
        ids = _ids(conn, [task.start.commander, *task.start.cards])
        deck_id = create_deck(conn, pool_id, name=f"eval {task.id}")
        start = DeckState.new(
            ids[task.start.commander], {ids[n]: c for n, c in task.start.cards.items()}
        )
        details["version"] = save_version(conn, deck_id, start)
        run = refine_deck(services, deck_id, request=task.request)
    legal = [
        p for p in deck_proposals(conn, deck_id) if p.run_id == run.run_id and p.status == "pending"
    ]
    details["deck_id"] = str(deck_id)
    scores: dict[str, float] = {}
    graded_before = grader.spent_usd
    if legal:
        proposal = legal[-1]
        state = DeckState.from_dict(proposal.payload["deck"])
        commander = conn.execute(
            "SELECT name FROM cards WHERE oracle_id = %s", (state.commander,)
        ).fetchone()
        details["proposal_id"] = str(proposal.id)
        details["commander"] = commander[0] if commander else None
        details["commander_match"] = (
            details["commander"] == task.commander if task.commander else None
        )
        details["deck_text"] = deck_text(conn, state, task.request, start)
        if start is not None and task.goals:
            details["goals"] = goal_results(conn, task.goals, start, state)
        try:
            grade = grade_deck(grader, details["deck_text"])
        except InvalidOutputError as exc:
            details["grading_error"] = str(exc)
        else:
            given = grade.scores.model_dump().items()
            scores = {name: float(score) for name, score in given if score is not None}
            scores["quality"] = grade.quality
            details["reasons"] = grade.reasons.model_dump()
            details["summary"] = grade.summary
        if "goals" in details:
            met = [goal["met"] for goal in details["goals"]]
            scores["goals_met"] = sum(met) / len(met)
    set_archived(conn, deck_id, True)
    record_audit(conn, "system", "archive", f"deck:{deck_id}", {"reason": "eval deck"})
    result = result_from_run(conn, run.run_id, task.id, success=bool(legal), details=details)
    result.scores = scores
    result.grading_cost_usd = grader.spent_usd - graded_before
    return result


def _final_deck(conn: psycopg.Connection, text: str) -> DeckState:
    """The deck a saved grader text shows, read back from its card lines."""
    commander = re.search(r"^Commander: (.+?) \| ", text, re.MULTILINE)
    if commander is None:
        raise ValueError("the saved deck text names no commander")
    cards = {
        m.group(2): int(m.group(1))
        for m in re.finditer(r"^(\d+) (.+?) \| ", text.split("\n\nLands: ")[0], re.MULTILINE)
    }
    ids = _ids(conn, [commander.group(1), *cards])
    return DeckState.new(ids[commander.group(1)], {ids[n]: c for n, c in cards.items()})


def check_goals(conn: psycopg.Connection, run: EvalRun, tasks: Sequence[DeckTask]) -> EvalRun:
    """A saved run with its refines' goals checked by code. Free: no model is called."""
    by_id = {t.id: t for t in tasks}
    checked = run.model_copy(deep=True)
    for result in checked.results:
        task = by_id.get(result.case_id)
        text = result.details.get("deck_text")
        if task is None or task.start is None or not task.goals or not text:
            continue
        goals = goal_results(
            conn, task.goals, _start_state(conn, task.start), _final_deck(conn, text)
        )
        result.details["goals"] = goals
        result.scores["goals_met"] = sum(g["met"] for g in goals) / len(goals)
    return checked


def regrade(
    conn: psycopg.Connection, grader: ModelClient, run: EvalRun, tasks: Sequence[DeckTask]
) -> EvalRun:
    """A saved run with its refines graded again on their change; nothing else is rerun.

    The agent isn't called: each refine's final deck is read back from its
    saved text, and graded with the starting deck and the change shown.
    """
    by_id = {t.id: t for t in tasks}
    results: list[CaseResult] = []
    for result in run.results:
        task = by_id.get(result.case_id)
        text = result.details.get("deck_text")
        if task is None or task.kind != "refine" or task.start is None or not text:
            results.append(result)
            continue
        start = _start_state(conn, task.start)
        new_text = deck_text(conn, _final_deck(conn, text), task.request, start)
        before = grader.spent_usd
        grade = grade_deck(grader, new_text)
        scores = {name: float(score) for name, score in grade.scores.model_dump().items() if score}
        scores["quality"] = grade.quality
        details = result.details | {
            "deck_text": new_text,
            "reasons": grade.reasons.model_dump(),
            "summary": grade.summary,
        }
        results.append(
            result.model_copy(
                update={
                    "scores": scores,
                    "details": details,
                    "grading_cost_usd": result.grading_cost_usd + grader.spent_usd - before,
                }
            )
        )
    variant = run.variant.model_copy(update={"name": f"{run.variant.name}-regraded"})
    return run.model_copy(
        update={"variant": variant, "results": results, "started_at": datetime.now(UTC)}
    )


# --- metrics and reports -------------------------------------------------------------------


def _mean(values: Sequence[float]) -> float:
    return statistics.mean(values) if values else 0.0


def deck_metrics(run: EvalRun) -> dict[str, float]:
    """Success over the tasks run; quality and each criterion over the graded decks."""
    ran = [r for r in run.results if r.status not in ("skipped", "error")]
    graded = [r for r in ran if any(name in r.scores for name in CRITERIA)]
    named = [r for r in ran if r.details.get("commander_match") is not None]
    metrics = {
        "success": _mean([float(r.success) for r in ran]),
        "quality": _mean(
            [statistics.mean(r.scores[c] for c in CRITERIA if c in r.scores) for r in graded]
        ),
        "commander_match": _mean([float(r.details["commander_match"]) for r in named]),
    }
    for name in CRITERIA:
        metrics[name] = _mean([r.scores[name] for r in graded if name in r.scores])
    checked = [r.scores["goals_met"] for r in ran if "goals_met" in r.scores]
    metrics["goals_met"] = _mean(checked)
    metrics["all_goals_met"] = _mean([float(share == 1.0) for share in checked])
    return metrics


def deck_section(runs: Sequence[EvalRun]) -> str:
    header = ["Variant", "Legal deck", "Quality", "Named commander used", *CRITERIA]
    lines = [
        "## Deck quality",
        "",
        "Graded 1-5 per rubric criterion; quality is the mean. Fit is over tasks with a request.",
        "",
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
    ]
    for run in runs:
        m = deck_metrics(run)
        cells = [
            run.variant.name,
            f"{m['success']:.0%}",
            f"{m['quality']:.2f}",
            f"{m['commander_match']:.0%}",
        ]
        cells += [f"{m[name]:.2f}" for name in CRITERIA]
        lines.append("| " + " | ".join(cells) + " |")
    goal_lines = _goal_lines(runs)
    return "\n".join(lines + goal_lines) + "\n"


def _goal_lines(runs: Sequence[EvalRun]) -> list[str]:
    """Refines' goals, checked by code: the share met, and each task's goals."""
    if not any("goals_met" in r.scores for run in runs for r in run.results):
        return []
    lines = [
        "",
        "## Refine goals, checked by code",
        "",
        "| Variant | Goals met | Refines meeting every goal |",
        "|---|---|---|",
    ]
    for run in runs:
        m = deck_metrics(run)
        lines.append(f"| {run.variant.name} | {m['goals_met']:.0%} | {m['all_goals_met']:.0%} |")
    for run in runs:
        lines += ["", f"**{run.variant.name}:**", ""]
        for r in run.results:
            for goal in r.details.get("goals", []):
                mark = "met" if goal["met"] else "**not met**"
                lines.append(f"- {r.case_id}: {goal['goal']}: {mark} ({goal['actual']})")
    return lines


def estimate_usd(variants: Sequence[Variant], tasks: Sequence[DeckTask]) -> float:
    return sum(
        AGENT_COST.get((v.model, t.kind), 0.3) + GRADING_COST for v in variants for t in tasks
    )


def main(argv: Sequence[str] | None = None) -> int:
    from mtg_deck_advisor.config import get_settings
    from mtg_deck_advisor.db.connection import connect
    from mtg_deck_advisor.llm.factory import build_embedder, build_provider
    from mtg_deck_advisor.llm.recording import DatabaseRecorder
    from mtg_deck_advisor.observability.logging import configure_logging
    from mtg_deck_advisor.retrieval.rerank import build_reranker

    parser = argparse.ArgumentParser(prog="python -m mtg_deck_advisor.evaluation.deck_tasks")
    parser.add_argument("--variant", action="append", choices=list(VARIANTS), default=[])
    parser.add_argument("--ids", nargs="*", help="only these task IDs")
    parser.add_argument("--budget", type=float, default=3.0, help="US dollars, for all variants")
    parser.add_argument("--yes", action="store_true", help="don't ask before spending")
    parser.add_argument(
        "--rerun", type=Path, help="run --ids again for a saved run, and merge them into it"
    )
    parser.add_argument(
        "--check", type=Path, help="check a saved run's refine goals by code, in place (free)"
    )
    parser.add_argument(
        "--regrade",
        type=Path,
        help="grade a saved run's refines again on their change, without the agent (paid)",
    )
    args = parser.parse_args(argv)
    if args.regrade:
        return _regrade_main(args.regrade, budget=args.budget, yes=args.yes)
    if args.check:
        return _check_main(args.check)
    if bool(args.variant) == bool(args.rerun):
        parser.error("give --variant to run, or --rerun RUN.json with --ids")
    if args.rerun and not args.ids:
        parser.error("--rerun needs --ids: the tasks to run again")

    settings = get_settings()
    configure_logging(settings)
    tasks = [t for t in load_tasks() if not args.ids or t.id in args.ids]
    saved = EvalRun.load(args.rerun) if args.rerun else None
    variants = [saved.variant] if saved else [VARIANTS[name] for name in args.variant]
    print(f"{len(tasks)} tasks x {len(variants)} variant(s), graded by {GRADER_MODEL}.")
    if not confirm_spend(estimate_usd(variants, tasks), budget_usd=args.budget, yes=args.yes):
        return 1

    runs: list[EvalRun] = []
    with connect(settings) as conn:
        recorder = DatabaseRecorder(settings)
        grader = ModelClient(
            build_provider(settings), GRADER_MODEL, recorder=recorder, cost_cap_usd=args.budget
        )
        embedder, reranker = build_embedder(settings), build_reranker(settings)
        for variant in variants:

            def services_for(variant: Variant = variant) -> AgentServices:
                return variant_services(
                    conn,
                    variant,
                    provider=build_provider(settings),
                    embedder=embedder,
                    reranker=reranker,
                    recorder=recorder,
                    cost_cap_usd=settings.agent_cost_cap_usd,
                    max_turns=settings.agent_max_turns,
                )

            run = run_suite(
                "deck_tasks",
                tasks,
                variant,
                make_evaluator(conn, services_for, grader),
                budget_usd=args.budget / len(variants),
                case_id=lambda task: task.id,
            )
            if saved is not None and args.rerun is not None:
                run = merge_results(saved, run)
                run.save(args.rerun.parent.parent)  # back to the same file
                print(f"{variant.name}: merged into {args.rerun}")
            else:
                print(f"{variant.name}: saved {run.save()}, spent ${run.spent_usd:.4f}")
            runs.append(run)

    notes = (
        f"Tasks: `{DECK_TASKS.as_posix()}` ({len(load_tasks())}). Quality graded by "
        f"{GRADER_MODEL} with [the rubric](../rubrics/{RUBRIC.name}); legality is checked by code."
    )
    if saved is not None:
        notes += f" Run again and merged: {', '.join(sorted(t.id for t in tasks))}."
    report = comparison_report("Deck tasks", runs, notes=notes) + "\n" + deck_section(runs)
    path = save_report(report, "deck-tasks")
    print(report)
    print(f"saved {path}")
    return 0


def _check_main(path: Path) -> int:
    from mtg_deck_advisor.config import get_settings
    from mtg_deck_advisor.db.connection import connect

    with connect(get_settings()) as conn:
        run = check_goals(conn, EvalRun.load(path), load_tasks())
    run.save(path.parent.parent)  # back to the same file
    # Windows consoles may not print "→"; the run file has the full text.
    text = "\n".join(_goal_lines([run]))
    print(
        text.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(
            sys.stdout.encoding or "utf-8"
        )
    )
    return 0


def _regrade_main(path: Path, *, budget: float, yes: bool) -> int:
    from mtg_deck_advisor.config import get_settings
    from mtg_deck_advisor.db.connection import connect
    from mtg_deck_advisor.llm.factory import build_provider
    from mtg_deck_advisor.llm.recording import DatabaseRecorder
    from mtg_deck_advisor.observability.logging import configure_logging

    settings = get_settings()
    configure_logging(settings)
    saved = EvalRun.load(path)
    tasks = load_tasks()
    refines = {t.id for t in tasks if t.kind == "refine"}
    count = sum(1 for r in saved.results if r.case_id in refines and r.details.get("deck_text"))
    print(f"{count} refines in {path} to grade again, by {GRADER_MODEL}.")
    if not confirm_spend(count * GRADING_COST, budget_usd=budget, yes=yes):
        return 1
    with connect(settings) as conn:
        grader = ModelClient(
            build_provider(settings),
            GRADER_MODEL,
            recorder=DatabaseRecorder(settings),
            cost_cap_usd=budget,
        )
        run = regrade(conn, grader, saved, tasks)
    print(f"saved {run.save()}, grading cost ${grader.spent_usd:.4f}")
    notes = (
        f"Refines from `{path.as_posix()}` graded again by {GRADER_MODEL}, with the starting deck "
        f"and the change shown ([the rubric](../rubrics/{RUBRIC.name})); the agent wasn't rerun."
    )
    report = comparison_report("Deck tasks", [run], notes=notes) + "\n" + deck_section([run])
    print(f"saved {save_report(report, 'deck-tasks')}")
    return 0


def make_evaluator(
    conn: psycopg.Connection,
    services_for: Callable[[], AgentServices],
    grader: ModelClient,
    *,
    datasets: Path = DATASETS,
) -> Callable[[DeckTask], CaseResult]:
    """Run each task with services of its own, then commit.

    Fresh services mean a fresh model client, so each run has its own cost
    cap, as a run through the API does; one client shared by the suite would
    spend the cap across every task. Committing keeps each task's records
    whatever happens to the next.
    """
    pools: dict[str, UUID] = {}

    def evaluate(task: DeckTask) -> CaseResult:
        result = run_task(services_for(), grader, task, datasets=datasets, pools=pools)
        conn.commit()
        return result

    return evaluate


if __name__ == "__main__":
    sys.exit(main())
