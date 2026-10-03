"""Scoring retrieval against labelled relevant items: recall@k and MRR (EVL-1).

- **recall@k:** the share of a query's relevant items that appear in its
  top k results. It answers "did search find what it should?".
- **MRR (mean reciprocal rank):** the average over queries of 1 / the rank of
  the first relevant result, so 1 when it's first, 0.5 when second. It
  answers "how soon does the first right answer appear?". A query with
  nothing relevant in the top 10 scores 0 (MRR@10): 10 is the most a search
  returns to its caller, so anything later is never seen.
"""

from collections.abc import Hashable, Sequence, Set
from statistics import mean

from pydantic import BaseModel

MRR_CUTOFF = 10


class RetrievalScore(BaseModel):
    queries: int
    recall_at_5: float
    recall_at_10: float
    mrr: float


def recall_at_k[T: Hashable](ranked: Sequence[T], relevant: Set[T], k: int) -> float:
    if not relevant:
        raise ValueError("a query needs at least one relevant item")
    return len(set(ranked[:k]) & relevant) / len(relevant)


def reciprocal_rank[T: Hashable](
    ranked: Sequence[T], relevant: Set[T], cutoff: int = MRR_CUTOFF
) -> float:
    for rank, item in enumerate(ranked[:cutoff], start=1):
        if item in relevant:
            return 1 / rank
    return 0.0


def score[T: Hashable](results: Sequence[tuple[Sequence[T], Set[T]]]) -> RetrievalScore:
    """Average recall@5, recall@10 and MRR over (ranked results, relevant items) pairs."""
    if not results:
        raise ValueError("no queries to score")
    return RetrievalScore(
        queries=len(results),
        recall_at_5=mean(recall_at_k(ranked, relevant, 5) for ranked, relevant in results),
        recall_at_10=mean(recall_at_k(ranked, relevant, 10) for ranked, relevant in results),
        mrr=mean(reciprocal_rank(ranked, relevant) for ranked, relevant in results),
    )
