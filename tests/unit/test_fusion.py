"""Reciprocal rank fusion: merging ranked lists by rank alone."""

import pytest

from mtg_deck_advisor.retrieval.fusion import reciprocal_rank_fusion


def test_one_list_keeps_its_order_with_scores_of_one_over_k_plus_rank() -> None:
    fused = reciprocal_rank_fusion([["a", "b"]])

    assert [item for item, _ in fused] == ["a", "b"]
    assert [score for _, score in fused] == pytest.approx([1 / 61, 1 / 62])


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
    fused = reciprocal_rank_fusion([["a", "b"]], k=1)

    assert fused[0][0] == "a"
    assert [score for _, score in fused] == pytest.approx([1 / 2, 1 / 3])


def test_nothing_to_fuse_gives_nothing() -> None:
    assert reciprocal_rank_fusion([[], []]) == []


def test_k_must_be_positive() -> None:
    with pytest.raises(ValueError, match="k"):
        reciprocal_rank_fusion([["a"]], k=0)


def test_weights_scale_each_rankings_contribution() -> None:
    fused = reciprocal_rank_fusion([["a", "b"], ["c"]], weights=[1.0, 0.5])

    # c is first in its list, but at half weight it scores below b's second place.
    assert [item for item, _ in fused] == ["a", "b", "c"]
    assert dict(fused)["c"] == pytest.approx(0.5 / 61)


def test_there_is_one_weight_per_ranking() -> None:
    with pytest.raises(ValueError, match="weight"):
        reciprocal_rank_fusion([["a"], ["b"]], weights=[1.0])
