"""Reciprocal rank fusion: merging ranked lists by rank alone."""

import pytest

from mtg_deck_advisor.retrieval.fusion import reciprocal_rank_fusion


def test_one_list_keeps_its_order_with_scores_of_one_over_k_plus_rank() -> None:
    fused = reciprocal_rank_fusion([["a", "b"]])

    assert fused == [("a", pytest.approx(1 / 61)), ("b", pytest.approx(1 / 62))]


def test_items_ranked_well_in_both_lists_come_first() -> None:
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["c", "a", "d"]])

    # a: 1/61 + 1/62, c: 1/63 + 1/61, b: 1/62, d: 1/63
    assert [item for item, _ in fused] == ["a", "c", "b", "d"]
    assert dict(fused)["a"] == pytest.approx(1 / 61 + 1 / 62)
    assert dict(fused)["c"] == pytest.approx(1 / 63 + 1 / 61)


def test_an_item_found_by_only_one_list_still_appears() -> None:
    fused = reciprocal_rank_fusion([["a"], ["b", "a"]])

    assert [item for item, _ in fused] == ["a", "b"]


def test_ties_keep_the_order_items_were_first_seen() -> None:
    fused = reciprocal_rank_fusion([["a", "x"], ["b", "y"]])

    assert [item for item, _ in fused] == ["a", "b", "x", "y"]


def test_a_smaller_k_weights_the_top_ranks_more() -> None:
    (top, top_score), (_, second_score) = reciprocal_rank_fusion([["a", "b"]], k=1)

    assert top == "a"
    assert (top_score, second_score) == (pytest.approx(1 / 2), pytest.approx(1 / 3))


def test_nothing_to_fuse_gives_nothing() -> None:
    assert reciprocal_rank_fusion([[], []]) == []


def test_k_must_be_positive() -> None:
    with pytest.raises(ValueError, match="k"):
        reciprocal_rank_fusion([["a"]], k=0)
