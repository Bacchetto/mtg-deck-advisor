"""Blind hand grading of decks, and how well the model grader agrees (#122, EVL-3).

    python -m mtg_deck_advisor.evaluation.hand_grade evals/runs/deck_tasks/*.json
    python -m mtg_deck_advisor.evaluation.hand_grade --agreement evals/runs/deck_tasks/*.json
    python -m mtg_deck_advisor.evaluation.hand_grade --rules evals/runs/rules_qa/*.json

The owner grades the same decks the model graded, with the same rubric
(`evals/rubrics/deck_quality.md`), seeing what the model grader saw: the
request and the deck. They don't see the model's scores, its reasons, or
which variant built the deck, and decks from all the runs given come in one
shuffled order. Grades are appended to `evals/hand_grades/decks.jsonl` as
they're given, so a session can stop at any deck and resume later.

Agreement is reported per criterion and over all scores: the share of exact
matches, the share within one point, and Cohen's kappa with quadratic
weights (1 is perfect agreement, 0 is what chance would give, and a larger
disagreement counts for more).
"""

import argparse
import json
import random
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from pydantic import BaseModel

from mtg_deck_advisor.evaluation.criteria import CRITERIA, LABELS
from mtg_deck_advisor.evaluation.results import CaseResult, EvalRun

HAND_GRADES = Path("evals/hand_grades/decks.jsonl")
SCALE = range(1, 6)


class HandGrade(BaseModel):
    # The run file the deck came from, and its task.
    run: str
    case_id: str
    scores: dict[str, int | None]
    grader: str
    graded_at: datetime


def load_hand_grades(path: Path = HAND_GRADES) -> list[HandGrade]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [HandGrade.model_validate_json(line) for line in lines if line.strip()]


def parse_scores(text: str, *, needs_fit: bool) -> dict[str, int | None]:
    """Scores typed in rubric order ("4 3 5 2 3 4"), leaving out fit when there's no request."""
    criteria = [c for c in CRITERIA if needs_fit or c != "fit"]
    parts = text.replace(",", " ").split()
    if len(parts) != len(criteria):
        raise ValueError(f"expected {len(criteria)} scores ({', '.join(criteria)})")
    if not all(part.isdigit() and int(part) in SCALE for part in parts):
        raise ValueError("each score must be a whole number from 1 to 5")
    scores: dict[str, int | None] = dict.fromkeys(CRITERIA, None)
    scores.update({name: int(part) for name, part in zip(criteria, parts, strict=True)})
    return scores


def blind_queue(
    runs: Mapping[str, EvalRun], done: set[tuple[str, str]], seed: int = 0
) -> list[tuple[str, CaseResult]]:
    """Every graded deck not yet hand-graded, from all the runs, in one shuffled order."""
    queue = [
        (path, result)
        for path, run in sorted(runs.items())
        for result in run.results
        if result.success and "deck_text" in result.details and (path, result.case_id) not in done
    ]
    random.Random(seed).shuffle(queue)  # noqa: S311 (an order to grade in, not a secret)
    return queue


def grade_session(
    queue: Sequence[tuple[str, CaseResult]],
    runs: Mapping[str, EvalRun],
    *,
    ask: Callable[[str], str] = input,
    out: TextIO = sys.stdout,
    path: Path = HAND_GRADES,
    grader: str = "owner",
) -> int:
    """Show each deck and save the scores typed for it. An empty answer ends the session."""
    path.parent.mkdir(parents=True, exist_ok=True)
    saved = 0
    for number, (run_path, result) in enumerate(queue, 1):
        needs_fit = bool(result.details.get("request"))
        criteria = [LABELS[c] for c in CRITERIA if needs_fit or c != "fit"]
        out.write(f"\n=== Deck {number} of {len(queue)} ===\n\n{result.details['deck_text']}\n\n")
        out.write(f"Score 1-5, in this order: {', '.join(criteria)}. Enter to stop.\n")
        while True:
            answer = ask("> ").strip()
            if not answer:
                out.write(f"Stopped. {saved} graded this session.\n")
                return saved
            try:
                scores = parse_scores(answer, needs_fit=needs_fit)
            except ValueError as exc:
                out.write(f"{exc}; try again.\n")
                continue
            break
        grade = HandGrade(
            run=run_path,
            case_id=result.case_id,
            scores=scores,
            grader=grader,
            graded_at=datetime.now(UTC),
        )
        with path.open("a", encoding="utf-8") as lines:
            lines.write(grade.model_dump_json() + "\n")
        saved += 1
    out.write(f"All done. {saved} graded this session.\n")
    return saved


# --- agreement -----------------------------------------------------------------------------


class Agreement(BaseModel):
    n: int
    exact: float
    within_one: float
    kappa: float


def _kappa(pairs: Sequence[tuple[int, int]], k: int = len(SCALE)) -> float:
    """Cohen's kappa with quadratic weights, over a 1..k scale (the 1-5 one by default)."""
    n = len(pairs)
    observed = [[0.0] * k for _ in range(k)]
    for a, b in pairs:
        observed[a - 1][b - 1] += 1 / n
    rows = [sum(row) for row in observed]
    cols = [sum(observed[i][j] for i in range(k)) for j in range(k)]
    weight = [[(i - j) ** 2 / (k - 1) ** 2 for j in range(k)] for i in range(k)]
    disagree = sum(weight[i][j] * observed[i][j] for i in range(k) for j in range(k))
    chance = sum(weight[i][j] * rows[i] * cols[j] for i in range(k) for j in range(k))
    if chance == 0:
        return 1.0 if disagree == 0 else 0.0
    return 1 - disagree / chance


def agreement(
    model: Sequence[Mapping[str, int | None]],
    hand: Sequence[Mapping[str, int | None]],
    criteria: Sequence[str] = CRITERIA,
) -> dict[str, Agreement]:
    """Per criterion, and "all" over every score: how often the two graders agree."""
    by_criterion: dict[str, list[tuple[int, int]]] = {name: [] for name in criteria}
    for m, h in zip(model, hand, strict=True):
        for name in criteria:
            a, b = m.get(name), h.get(name)
            if a is not None and b is not None:
                by_criterion[name].append((a, b))
    by_criterion["all"] = [pair for name in criteria for pair in by_criterion[name]]
    return {
        name: Agreement(
            n=len(pairs),
            exact=sum(a == b for a, b in pairs) / len(pairs),
            within_one=sum(abs(a - b) <= 1 for a, b in pairs) / len(pairs),
            kappa=_kappa(pairs),
        )
        for name, pairs in by_criterion.items()
        if pairs
    }


def agreement_section(runs: Mapping[str, EvalRun], grades: Sequence[HandGrade]) -> str:
    results = {(path, r.case_id): r for path, run in runs.items() for r in run.results}
    model: list[dict[str, int | None]] = []
    hand: list[dict[str, int | None]] = []
    for grade in grades:
        result = results.get((grade.run, grade.case_id))
        if result is None:
            continue
        model.append({c: int(result.scores[c]) if c in result.scores else None for c in CRITERIA})
        hand.append(grade.scores)
    lines = [
        "## Agreement with the owner's hand grades",
        "",
        f"{len(hand)} decks graded by both, blind to each other.",
        "",
        "| Criterion | Scores | Exact | Within one | Weighted kappa |",
        "|---|---|---|---|---|",
    ]
    for name, a in agreement(model, hand).items():
        label = LABELS.get(name, "all scores")
        lines.append(f"| {label} | {a.n} | {a.exact:.0%} | {a.within_one:.0%} | {a.kappa:.2f} |")
    return "\n".join(lines) + "\n"


# --- rules answers ---------------------------------------------------------------------------

RULES_HAND_GRADES = Path("evals/hand_grades/rules.jsonl")
RULES_QA = Path("evals/datasets/rules_qa.jsonl")
VERDICTS = {"c": "correct", "p": "partly correct", "i": "incorrect"}
# Ordered from worst to best, for kappa's 1..3 scale.
VERDICT_ORDER = ("incorrect", "partly correct", "correct")


class RulesHandGrade(BaseModel):
    run: str
    case_id: str
    verdict: str
    grader: str
    graded_at: datetime


def load_rules_grades(path: Path = RULES_HAND_GRADES) -> list[RulesHandGrade]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [RulesHandGrade.model_validate_json(line) for line in lines if line.strip()]


def parse_verdict(text: str) -> str:
    """A verdict typed as its letter or in full."""
    typed = text.strip().casefold()
    if typed in VERDICTS:
        return VERDICTS[typed]
    if typed in VERDICTS.values():
        return typed
    raise ValueError("type c, p or i (correct, partly correct, incorrect)")


def rules_queue(
    runs: Mapping[str, EvalRun], done: set[tuple[str, str]], seed: int = 0
) -> list[tuple[str, CaseResult]]:
    """Every answer the model grader graded, from all the runs, in one shuffled order."""
    queue = [
        (path, result)
        for path, run in sorted(runs.items())
        for result in run.results
        if "verdict" in result.details and (path, result.case_id) not in done
    ]
    random.Random(seed).shuffle(queue)  # noqa: S311 (an order to grade in, not a secret)
    return queue


def _questions(path: Path) -> dict[str, dict[str, Any]]:
    """The rules Q&A set by ID, read as plain JSON (loading rules_qa would load the agent)."""
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return {row["id"]: row for row in rows}


def rules_session(
    queue: Sequence[tuple[str, CaseResult]],
    *,
    ask: Callable[[str], str] = input,
    out: TextIO = sys.stdout,
    path: Path = RULES_HAND_GRADES,
    grader: str = "owner",
    dataset: Path = RULES_QA,
) -> int:
    """Show each answer with its question and key facts, and save the verdict typed for it."""
    questions = _questions(dataset)
    path.parent.mkdir(parents=True, exist_ok=True)
    saved = 0
    for number, (run_path, result) in enumerate(queue, 1):
        case = questions[result.case_id]
        facts = "\n".join(f"  - {fact}" for fact in case["key_facts"])
        out.write(
            f"\n=== Answer {number} of {len(queue)} ===\n\nQuestion: {case['question']}\n\n"
            f"Key facts:\n{facts}\n\nAnswer:\n{result.details.get('answer')}\n\n"
            "Grade it by evals/rubrics/rules_answer.md: c = correct, p = partly correct, "
            "i = incorrect. Enter to stop.\n"
        )
        while True:
            answer = ask("> ").strip()
            if not answer:
                out.write(f"Stopped. {saved} graded this session.\n")
                return saved
            try:
                verdict = parse_verdict(answer)
            except ValueError as exc:
                out.write(f"{exc}.\n")
                continue
            break
        grade = RulesHandGrade(
            run=run_path,
            case_id=result.case_id,
            verdict=verdict,
            grader=grader,
            graded_at=datetime.now(UTC),
        )
        with path.open("a", encoding="utf-8") as lines:
            lines.write(grade.model_dump_json() + "\n")
        saved += 1
    out.write(f"All done. {saved} graded this session.\n")
    return saved


def rules_agreement(
    runs: Mapping[str, EvalRun], grades: Sequence[RulesHandGrade | Mapping[str, Any]]
) -> Agreement:
    """How often the owner's verdicts match the model grader's: exact, within one, kappa."""
    results = {(path, r.case_id): r for path, run in runs.items() for r in run.results}
    pairs: list[tuple[int, int]] = []
    for grade in grades:
        given = grade if isinstance(grade, Mapping) else grade.model_dump()
        result = results.get((given["run"], given["case_id"]))
        if result is None or "verdict" not in result.details:
            continue
        model = VERDICT_ORDER.index(result.details["verdict"]) + 1
        pairs.append((model, VERDICT_ORDER.index(given["verdict"]) + 1))
    if not pairs:
        return Agreement(n=0, exact=0.0, within_one=0.0, kappa=0.0)
    return Agreement(
        n=len(pairs),
        exact=sum(a == b for a, b in pairs) / len(pairs),
        within_one=sum(abs(a - b) <= 1 for a, b in pairs) / len(pairs),
        kappa=_kappa(pairs, k=len(VERDICT_ORDER)),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mtg_deck_advisor.evaluation.hand_grade")
    parser.add_argument("runs", nargs="+", type=Path, help="deck task run files")
    parser.add_argument("--grader", default="owner")
    parser.add_argument("--agreement", action="store_true", help="report agreement instead")
    parser.add_argument("--rules", action="store_true", help="grade rules answers, not decks")
    args = parser.parse_args(argv)

    runs = {path.as_posix(): EvalRun.load(path) for path in args.runs}
    if args.rules:
        rules_grades = load_rules_grades()
        if args.agreement:
            a = rules_agreement(runs, rules_grades)
            print(
                f"{a.n} answers graded by both: exact {a.exact:.0%}, "
                f"within one {a.within_one:.0%}, weighted kappa {a.kappa:.2f}"
            )
            return 0
        done = {(g.run, g.case_id) for g in rules_grades}
        rules_session(rules_queue(runs, done), grader=args.grader)
        return 0
    grades = load_hand_grades()
    if args.agreement:
        print(agreement_section(runs, grades))
        return 0
    done = {(g.run, g.case_id) for g in grades}
    grade_session(blind_queue(runs, done), runs, grader=args.grader)
    return 0


if __name__ == "__main__":
    sys.exit(main())
