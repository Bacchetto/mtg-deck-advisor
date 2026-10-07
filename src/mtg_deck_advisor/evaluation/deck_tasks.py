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
import statistics
import sys
from collections.abc import Callable, Sequence
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


# --- what the grader sees --------------------------------------------------------------------


def deck_text(conn: psycopg.Connection, state: DeckState, request: str) -> str:
    """The deck as the grader reads it: the request, every card's text, and the counts."""
    ids = [state.commander, *state.cards]
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

    lands = sum(n for card, n in state.cards.items() if "Land" in cards[card][3])
    curve: dict[int, int] = {}
    for card, n in state.cards.items():
        if "Land" not in cards[card][3]:
            value = int(cards[card][2] or 0)
            curve[min(value, 7)] = curve.get(min(value, 7), 0) + n
    curve_text = ", ".join(
        f"{value if value < 7 else '7+'}: {curve[value]}" for value in sorted(curve)
    )
    others = sorted(state.cards.items(), key=lambda item: cards[item[0]][0])
    return "\n".join(
        [
            f"Request: {request or 'none'}",
            "",
            f"Commander: {line(state.commander, 1)[2:]}",
            "",
            f"The other {state.total - 1} cards (count | name | mana cost | type | text):",
            *(line(card, n) for card, n in others),
            "",
            f"Lands: {lands}. Nonland cards by mana value: {curve_text}.",
        ]
    )


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
        details["deck_text"] = deck_text(conn, state, task.request)
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
    set_archived(conn, deck_id, True)
    record_audit(conn, "system", "archive", f"deck:{deck_id}", {"reason": "eval deck"})
    result = result_from_run(conn, run.run_id, task.id, success=bool(legal), details=details)
    result.scores = scores
    result.grading_cost_usd = grader.spent_usd - graded_before
    return result


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
    return "\n".join(lines) + "\n"


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
    args = parser.parse_args(argv)
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
