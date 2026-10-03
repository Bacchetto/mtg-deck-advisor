"""Comparing two retrieval runs query by query, and the rule for adopting a variant."""

from pathlib import Path

import pytest

from mtg_deck_advisor.evaluation.retrieval import (
    Comparison,
    QueryResult,
    RunResult,
    adoption_decision,
    bootstrap_interval,
    compare,
)


def result(ranked: list[str], relevant: list[str], seconds: float = 0.1) -> QueryResult:
    return QueryResult(ranked=ranked, relevant=relevant, seconds=seconds)


def test_wins_losses_and_ties_are_counted_per_query() -> None:
    baseline = {
        "q1": result(["x", "a"], ["a"]),  # found at 2
        "q2": result(["a"], ["a"]),  # found at 1
        "q3": result(["x"], ["a"]),  # not found
        "q4": result(["a", "b"], ["a", "b"]),
    }
    candidate = {
        "q1": result(["a", "x"], ["a"]),  # same recall, better rank: a win
        "q2": result(["x", "a"], ["a"]),  # same recall, worse rank: a loss
        "q3": result(["a"], ["a"]),  # found now: a win
        "q4": result(["a", "b"], ["a", "b"]),  # identical: a tie
    }

    comparison = compare(baseline, candidate)

    assert (comparison.wins, comparison.losses, comparison.ties) == (2, 1, 1)
    assert comparison.queries == 4


def test_the_mean_differences_are_candidate_minus_baseline() -> None:
    baseline = {"q1": result(["x"], ["a"]), "q2": result(["a"], ["a"])}
    candidate = {"q1": result(["a"], ["a"]), "q2": result(["a"], ["a"])}

    comparison = compare(baseline, candidate)

    assert comparison.recall_delta == pytest.approx(0.5)
    assert comparison.mrr_delta == pytest.approx(0.5)


def test_runs_must_cover_the_same_queries() -> None:
    with pytest.raises(ValueError, match="same queries"):
        compare({"q1": result(["a"], ["a"])}, {"q2": result(["a"], ["a"])})


def test_the_bootstrap_interval_is_repeatable_and_contains_the_mean() -> None:
    values = [0.0, 0.0, 1.0, 0.5, -0.5, 1.0, 0.0, 0.25]

    low, high = bootstrap_interval(values, seed=1)

    assert (low, high) == bootstrap_interval(values, seed=1)
    assert low <= sum(values) / len(values) <= high
    assert low < high


def test_identical_differences_give_a_zero_width_interval() -> None:
    assert bootstrap_interval([0.2] * 10) == (pytest.approx(0.2), pytest.approx(0.2))


def comparison_with(recall: float, mrr: float, wins: int, losses: int) -> Comparison:
    baseline = {f"q{i}": result(["x"], ["a"]) for i in range(10)}
    return compare(baseline, baseline).model_copy(
        update={"recall_delta": recall, "mrr_delta": mrr, "wins": wins, "losses": losses}
    )


@pytest.mark.parametrize(
    ("recall", "mrr", "wins", "losses", "seconds", "adopt"),
    [
        (0.03, 0.00, 5, 2, 0.5, True),  # recall up 3 points
        (0.00, 0.04, 5, 2, 0.5, True),  # MRR up 4 points
        (0.02, 0.02, 5, 2, 0.5, False),  # neither up 3 points
        (0.05, 0.00, 3, 3, 0.5, False),  # no more wins than losses
        (0.05, -0.03, 5, 2, 0.5, False),  # the other metric drops more than 2 points
        (0.05, -0.02, 5, 2, 0.5, True),  # a 2-point drop is allowed
        (0.05, 0.00, 5, 2, 2.5, False),  # over the 2 s latency budget
    ],
)
def test_the_adoption_rule(
    recall: float, mrr: float, wins: int, losses: int, seconds: float, adopt: bool
) -> None:
    decision = adoption_decision(comparison_with(recall, mrr, wins, losses), seconds)

    assert decision.adopt is adopt
    assert decision.reasons  # every decision says why


def test_a_run_round_trips_through_json(tmp_path: Path) -> None:
    run = RunResult(
        variant="baseline",
        set_name="dev",
        model="qwen3-embedding:0.6b",
        created="2026-10-02T12:00:00",
        cards={"C01": result(["a", "b"], ["a"], 0.08)},
        rules={"R01": result(["903.5a"], ["903.5a"], 0.02)},
    )
    path = tmp_path / "run.json"

    run.save(path)

    assert RunResult.load(path) == run
