"""Reciprocal rank fusion (RRF): merging ranked lists by rank alone (RAG-5).

Vector search scores cosine similarity and keyword search scores term
matches. The two scales aren't comparable, so adding or averaging the scores
would mean calibrating one against the other. RRF ignores the scores and uses
only each item's rank in each list:

    score(item) = sum over lists of 1 / (k + rank in that list)

An item near the top of either list scores well, and one near the top of
both scores best. k (60, from the original paper, Cormack et al. 2009) damps
the difference between the very top ranks, so one list's first place doesn't
outweigh agreement between the two.
"""

from collections.abc import Hashable, Sequence

DEFAULT_K = 60


def reciprocal_rank_fusion[T: Hashable](
    rankings: Sequence[Sequence[T]],
    k: int = DEFAULT_K,
    weights: Sequence[float] | None = None,
) -> list[tuple[T, float]]:
    """Every item in any ranking, with its fused score, best first.

    `weights` scales each ranking's contribution (weight / (k + rank)); by
    default every ranking counts equally. Ties keep the order in which items
    were first seen, taking the rankings rank by rank, so the result is
    deterministic.
    """
    if k <= 0:
        raise ValueError("k must be positive")
    if weights is None:
        weights = [1.0] * len(rankings)
    if len(weights) != len(rankings):
        raise ValueError(
            f"{len(rankings)} rankings need {len(rankings)} weights, not {len(weights)}"
        )
    scores: dict[T, float] = {}
    first_seen: dict[T, tuple[int, int]] = {}
    for list_index, (ranking, weight) in enumerate(zip(rankings, weights, strict=True)):
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + weight / (k + rank)
            first_seen.setdefault(item, (rank, list_index))
    return sorted(scores.items(), key=lambda pair: (-pair[1], first_seen[pair[0]]))
