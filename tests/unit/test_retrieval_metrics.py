"""Retrieval metrics: recall@k and mean reciprocal rank (EVL-1)."""

import pytest

from mtg_deck_advisor.evaluation.retrieval import recall_at_k, reciprocal_rank, score


def test_recall_at_k_is_the_share_of_relevant_items_in_the_top_k() -> None:
    ranked = ["a", "x", "b", "y", "c"]

    assert recall_at_k(ranked, {"a", "b", "c", "d"}, k=3) == 0.5
    assert recall_at_k(ranked, {"a", "b", "c", "d"}, k=5) == 0.75


def test_recall_counts_nothing_past_k() -> None:
    assert recall_at_k(["x", "y", "a"], {"a"}, k=2) == 0.0


def test_recall_needs_something_relevant() -> None:
    with pytest.raises(ValueError, match="relevant"):
        recall_at_k(["a"], set(), k=5)


def test_reciprocal_rank_is_one_over_the_first_relevant_rank() -> None:
    assert reciprocal_rank(["a", "b"], {"a"}) == 1.0
    assert reciprocal_rank(["x", "y", "a", "b"], {"a", "b"}) == pytest.approx(1 / 3)


def test_reciprocal_rank_is_zero_when_nothing_relevant_is_in_the_cutoff() -> None:
    ranked = [f"x{i}" for i in range(10)] + ["a"]

    assert reciprocal_rank(ranked, {"a"}) == 0.0  # rank 11, past the cutoff of 10
    assert reciprocal_rank(ranked, {"a"}, cutoff=11) == pytest.approx(1 / 11)


def test_scores_are_averaged_over_queries() -> None:
    result = score(
        [
            (["a", "b"], {"a"}),  # recall 1 at 5 and 10, reciprocal rank 1
            (["x", "a"], {"a", "b"}),  # recall 0.5, reciprocal rank 1/2
        ]
    )

    assert result.queries == 2
    assert result.recall_at_5 == pytest.approx(0.75)
    assert result.recall_at_10 == pytest.approx(0.75)
    assert result.mrr == pytest.approx(0.75)


def test_no_queries_has_no_score() -> None:
    with pytest.raises(ValueError, match="no queries"):
        score([])
