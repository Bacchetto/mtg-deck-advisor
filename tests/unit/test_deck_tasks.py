"""The deck task set, deck grades, agreement statistics and blind hand grading (#122, EVL-3)."""

import io
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from mtg_deck_advisor.evaluation.deck_tasks import (
    CRITERIA,
    DeckGrade,
    deck_metrics,
    load_tasks,
)
from mtg_deck_advisor.evaluation.hand_grade import (
    HandGrade,
    agreement,
    blind_queue,
    grade_session,
    load_hand_grades,
    parse_scores,
)
from mtg_deck_advisor.evaluation.runner import CaseResult, EvalRun, Variant
from mtg_deck_advisor.llm.anthropic import strict_schema

STARTED = datetime(2026, 10, 7, tzinfo=UTC)


def graded(case_id: str, scores: Mapping[str, int | None], *, success: bool = True) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        status="completed",
        success=success,
        details={"deck_text": f"deck for {case_id}", "request": "", "commander_match": None},
        scores={k: float(v) for k, v in scores.items() if v is not None},
    )


def run(name: str, *results: CaseResult) -> EvalRun:
    return EvalRun(
        suite="deck_tasks",
        variant=Variant(name=name, model="claude-sonnet-5-5"),
        started_at=STARTED,
        budget_usd=3.0,
        results=list(results),
    )


ALL_FOUR = dict.fromkeys(CRITERIA, 4)


# --- the set and the grade -------------------------------------------------------------------


def test_the_task_set_covers_each_pool_and_kind_of_request() -> None:
    tasks = load_tasks()

    assert [t.id for t in tasks] == [f"T{n:02}" for n in range(1, 11)]
    assert {t.pool for t in tasks} == {
        "pool_300.txt",
        "pool_300_test.txt",
        "pool_tcgplayer_collection.csv",
    }
    assert all(t.start is not None for t in tasks if t.kind == "refine")
    assert {t.commander for t in tasks if t.commander} == {
        "Pia and Kiran Nalaar",
        "Gisa and Geralf",
    }


def test_a_grade_is_1_to_5_and_fit_may_be_skipped() -> None:
    grade = DeckGrade(
        scores={**ALL_FOUR, "fit": None},
        reasons=dict.fromkeys(CRITERIA, "Because."),
        summary="Solid.",
    )

    assert grade.quality == 4.0  # the mean of the scored criteria
    with pytest.raises(ValidationError):
        DeckGrade(scores={**ALL_FOUR, "ramp": 6}, reasons={}, summary="")
    with pytest.raises(ValidationError):
        DeckGrade(scores={"plan": 4}, reasons={}, summary="")  # every criterion is needed


def test_the_graders_schema_names_every_criterion() -> None:
    # Structured output forbids keys a schema doesn't declare, so an open
    # dict of scores could only ever come back empty ({}), as it did live.
    schema = strict_schema(DeckGrade.model_json_schema())
    defs = schema["$defs"]

    for field in ("scores", "reasons"):
        target = defs[schema["properties"][field]["$ref"].rsplit("/", 1)[1]]
        assert set(target["properties"]) == set(CRITERIA), field
        assert set(target["required"]) == set(CRITERIA), field


def test_deck_metrics_report_success_quality_and_each_criterion() -> None:
    metrics = deck_metrics(
        run(
            "sonnet",
            graded("T01", {**ALL_FOUR, "fit": None}),
            graded("T02", {**dict.fromkeys(CRITERIA, 2), "fit": 5}),
            CaseResult(case_id="T03", status="completed", success=False),
        )
    )

    assert metrics["success"] == pytest.approx(2 / 3)
    assert metrics["quality"] == pytest.approx((4.0 + 15 / 6) / 2)
    assert metrics["fit"] == 5.0  # only T02 had a request
    assert metrics["ramp"] == 3.0


# --- hand grading --------------------------------------------------------------------------


def test_scores_are_typed_as_numbers_in_rubric_order() -> None:
    assert parse_scores("4 3 5 2 3 4", needs_fit=True) == {
        "plan": 4,
        "fit": 3,
        "mana": 5,
        "ramp": 2,
        "draw": 3,
        "interaction": 4,
    }
    assert parse_scores("4 5 2 3 4", needs_fit=False)["fit"] is None
    with pytest.raises(ValueError, match="1 to 5"):
        parse_scores("4 3 9 2 3 4", needs_fit=True)
    with pytest.raises(ValueError, match="6 scores"):
        parse_scores("4 3", needs_fit=True)


def test_the_queue_is_blind_shuffled_and_skips_what_is_done() -> None:
    runs = {
        "a.json": run("sonnet", graded("T01", ALL_FOUR), graded("T02", ALL_FOUR)),
        "b.json": run("haiku", graded("T01", ALL_FOUR), graded("T02", ALL_FOUR, success=False)),
    }

    queue = blind_queue(runs, done={("a.json", "T02")}, seed=7)

    assert sorted((path, case.case_id) for path, case in queue) == [
        ("a.json", "T01"),
        ("b.json", "T01"),  # b's T02 failed, so it has no deck to grade
    ]
    assert blind_queue(runs, done=set(), seed=7) == blind_queue(runs, done=set(), seed=7)


def test_a_session_shows_only_the_deck_and_saves_each_grade(tmp_path: Path) -> None:
    runs = {"a.json": run("sonnet-secret-variant", graded("T01", ALL_FOUR))}
    out = io.StringIO()
    answers = iter(["4 5 2 3 4", ""])  # no request, so no fit score
    path = tmp_path / "hand.jsonl"

    grade_session(
        blind_queue(runs, set()),
        runs,
        ask=lambda _: next(answers),
        out=out,
        path=path,
        grader="owner",
    )

    shown = out.getvalue()
    assert "deck for T01" in shown
    assert (
        "sonnet-secret-variant" not in shown and "4.0" not in shown
    )  # no variant, no model scores
    (saved,) = load_hand_grades(path)
    assert (saved.run, saved.case_id, saved.grader) == ("a.json", "T01", "owner")
    assert saved.scores["ramp"] == 2


def test_agreement_is_exact_within_one_and_weighted_kappa() -> None:
    model = [{"plan": 5}, {"plan": 4}, {"plan": 2}, {"plan": 1}]
    same = agreement(model, model, ["plan"])["plan"]
    off = agreement(model, [{"plan": 5}, {"plan": 3}, {"plan": 2}, {"plan": 3}], ["plan"])["plan"]

    assert (same.n, same.exact, same.within_one) == (4, 1.0, 1.0)
    assert same.kappa == pytest.approx(1.0)
    assert (off.exact, off.within_one) == (0.5, 0.75)
    assert 0 < off.kappa < 1


def test_a_hand_grade_records_who_and_when() -> None:
    grade = HandGrade(
        run="a.json", case_id="T01", scores=ALL_FOUR, grader="owner", graded_at=STARTED
    )

    assert HandGrade.model_validate_json(grade.model_dump_json()) == grade
