"""Blind hand grading of rules answers, and how well the model grader agrees (#125, EVL-3)."""

import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mtg_deck_advisor.evaluation.hand_grade import (
    load_rules_grades,
    parse_verdict,
    rules_agreement,
    rules_queue,
    rules_session,
)
from mtg_deck_advisor.evaluation.results import CaseResult, EvalRun, Variant

STARTED = datetime(2026, 10, 7, tzinfo=UTC)


def answered(case_id: str, verdict: str | None, answer: str = "An answer.") -> CaseResult:
    details: dict[str, object] = {"answerable": True, "found": True, "answer": answer}
    if verdict:
        details["verdict"] = verdict
    return CaseResult(case_id=case_id, status="completed", success=True, details=details)


def run(name: str, *results: CaseResult) -> EvalRun:
    return EvalRun(
        suite="rules_qa",
        variant=Variant(name=name, model="claude-sonnet-5-5"),
        started_at=STARTED,
        budget_usd=1.0,
        results=list(results),
    )


def dataset(tmp_path: Path) -> Path:
    path = tmp_path / "rules_qa.jsonl"
    rows = [
        {"id": "R01", "question": "How many cards?", "key_facts": ["Exactly 100 cards."]},
        {"id": "R02", "question": "How many copies?", "key_facts": ["One of each."]},
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def test_verdicts_are_typed_as_a_letter_or_a_word() -> None:
    assert parse_verdict("c") == "correct"
    assert parse_verdict(" P ") == "partly correct"
    assert parse_verdict("incorrect") == "incorrect"
    with pytest.raises(ValueError, match="c, p or i"):
        parse_verdict("maybe")


def test_the_queue_holds_only_graded_answers_from_every_run_shuffled() -> None:
    runs = {
        "s.json": run("sonnet", answered("R01", "correct"), answered("R02", None)),
        "h.json": run("haiku", answered("R01", "incorrect"), answered("R02", "correct")),
    }

    queue = rules_queue(runs, done={("h.json", "R02")}, seed=3)

    assert sorted((path, r.case_id) for path, r in queue) == [("h.json", "R01"), ("s.json", "R01")]
    assert rules_queue(runs, set(), seed=3) == rules_queue(runs, set(), seed=3)


def test_a_session_shows_the_question_facts_and_answer_but_not_the_grade(tmp_path: Path) -> None:
    runs = {"s.json": run("sonnet-secret", answered("R01", "partly correct", "Exactly 100."))}
    out = io.StringIO()
    answers = iter(["x", "c", ""])
    path = tmp_path / "rules.jsonl"

    saved = rules_session(
        rules_queue(runs, set()),
        ask=lambda _: next(answers),
        out=out,
        path=path,
        grader="owner",
        dataset=dataset(tmp_path),
    )

    shown = out.getvalue()
    assert "How many cards?" in shown and "Exactly 100 cards." in shown and "Exactly 100." in shown
    assert "partly" not in shown.split("c = correct")[0]  # the model's verdict isn't shown
    assert "sonnet-secret" not in shown
    assert "c, p or i" in shown  # the bad answer was asked again
    assert saved == 1
    (grade,) = load_rules_grades(path)
    assert (grade.run, grade.case_id, grade.verdict, grade.grader) == (
        "s.json",
        "R01",
        "correct",
        "owner",
    )


def test_rules_agreement_is_exact_and_weighted_kappa() -> None:
    runs = {
        "s.json": run(
            "sonnet",
            *(
                answered(f"R0{n}", v)
                for n, v in enumerate(["correct", "correct", "partly correct", "incorrect"], 1)
            ),
        )
    }
    hand = [
        {"run": "s.json", "case_id": "R01", "verdict": "correct"},
        {"run": "s.json", "case_id": "R02", "verdict": "partly correct"},
        {"run": "s.json", "case_id": "R03", "verdict": "partly correct"},
        {"run": "s.json", "case_id": "R04", "verdict": "incorrect"},
    ]

    result = rules_agreement(runs, hand)

    assert result.n == 4
    assert result.exact == 0.75
    assert 0 < result.kappa < 1
