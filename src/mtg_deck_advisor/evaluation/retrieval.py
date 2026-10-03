"""Scoring retrieval against labelled relevant items: recall@k and MRR (EVL-1).

- **recall@k:** the share of a query's relevant items that appear in its
  top k results. It answers "did search find what it should?".
- **MRR (mean reciprocal rank):** the average over queries of 1 / the rank of
  the first relevant result, so 1 when it's first, 0.5 when second. It
  answers "how soon does the first right answer appear?". A query with
  nothing relevant in the top 10 scores 0 (MRR@10): 10 is the most a search
  returns to its caller, so anything later is never seen.

Experiments compare two saved runs query by query (`compare`) and apply an
adoption rule fixed before any experiment ran (`adoption_decision`).
"""

import random
from collections.abc import Hashable, Mapping, Sequence, Set
from pathlib import Path
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


# ------------------------------------------------------- comparing two runs

# The adoption rule, fixed before any experiment (#72). A variant replaces the
# current best only if it improves recall@10 or MRR by at least 3 points, wins
# more queries than it loses, drops neither metric by more than 2 points, and
# keeps the median search within the latency budget.
MIN_GAIN = 0.03
MAX_DROP = 0.02
LATENCY_BUDGET_SECONDS = 2.0
# Float slack, so a change of exactly 3 points counts as 3 points.
EPSILON = 1e-9


class QueryResult(BaseModel):
    """One query in one run: what came back, what should have, and how long it took."""

    ranked: list[str]
    relevant: list[str]
    seconds: float

    @property
    def recall(self) -> float:
        return recall_at_k(self.ranked, set(self.relevant), MRR_CUTOFF)

    @property
    def rr(self) -> float:
        return reciprocal_rank(self.ranked, set(self.relevant))


class RunResult(BaseModel):
    """A whole run of one variant over one set, saved so runs can be compared later."""

    variant: str
    set_name: str
    model: str
    created: str
    cards: dict[str, QueryResult]
    rules: dict[str, QueryResult]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "RunResult":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


class Comparison(BaseModel):
    queries: int
    # Candidate minus baseline, averaged over queries.
    recall_delta: float
    mrr_delta: float
    # 95% bootstrap intervals for those differences.
    recall_interval: tuple[float, float]
    mrr_interval: tuple[float, float]
    # Per query: a win is higher recall@10, or the same recall and a higher
    # reciprocal rank (the first right answer appears sooner).
    wins: int
    losses: int
    ties: int


class Decision(BaseModel):
    adopt: bool
    reasons: list[str]


def bootstrap_interval(
    values: Sequence[float], *, resamples: int = 10_000, seed: int = 0, level: float = 0.95
) -> tuple[float, float]:
    """A percentile bootstrap interval for the mean of `values`.

    Queries are resampled with replacement, many times, and the spread of the
    resampled means shows how much the mean could move with a different
    sample of queries. With 40-odd queries the intervals are wide; that's the
    honest size of the uncertainty.
    """
    rng = random.Random(seed)  # noqa: S311 - statistics, not security: seeded to be repeatable
    n = len(values)
    means = sorted(mean(rng.choices(values, k=n)) for _ in range(resamples))
    tail = (1 - level) / 2
    return means[int(tail * resamples)], means[min(resamples - 1, int((1 - tail) * resamples))]


def compare(
    baseline: Mapping[str, QueryResult],
    candidate: Mapping[str, QueryResult],
    *,
    resamples: int = 10_000,
    seed: int = 0,
) -> Comparison:
    """Pair two runs query by query (both over the same queries)."""
    if set(baseline) != set(candidate):
        raise ValueError("the runs must cover the same queries")
    ids = sorted(baseline)
    recall_deltas = [candidate[i].recall - baseline[i].recall for i in ids]
    rr_deltas = [candidate[i].rr - baseline[i].rr for i in ids]
    wins = losses = 0
    for recall_delta, rr_delta in zip(recall_deltas, rr_deltas, strict=True):
        key = (round(recall_delta, 9), round(rr_delta, 9))
        if key > (0, 0):
            wins += 1
        elif key < (0, 0):
            losses += 1
    return Comparison(
        queries=len(ids),
        recall_delta=mean(recall_deltas),
        mrr_delta=mean(rr_deltas),
        recall_interval=bootstrap_interval(recall_deltas, resamples=resamples, seed=seed),
        mrr_interval=bootstrap_interval(rr_deltas, resamples=resamples, seed=seed),
        wins=wins,
        losses=losses,
        ties=len(ids) - wins - losses,
    )


def adoption_decision(
    comparison: Comparison,
    median_seconds: float,
    budget_seconds: float = LATENCY_BUDGET_SECONDS,
) -> Decision:
    """Apply the adoption rule, saying which parts passed or failed."""
    gain = max(comparison.recall_delta, comparison.mrr_delta)
    drop = min(comparison.recall_delta, comparison.mrr_delta)
    checks = [
        (
            gain >= MIN_GAIN - EPSILON,
            f"best gain {gain * 100:+.1f} points (needs +{MIN_GAIN * 100:.0f})",
        ),
        (
            comparison.wins > comparison.losses,
            f"{comparison.wins} wins, {comparison.losses} losses (needs more wins)",
        ),
        (
            drop >= -MAX_DROP - EPSILON,
            f"worst change {drop * 100:+.1f} points (may drop at most {MAX_DROP * 100:.0f})",
        ),
        (
            median_seconds <= budget_seconds,
            f"median search {median_seconds * 1000:.0f} ms (budget {budget_seconds:.0f} s)",
        ),
    ]
    return Decision(
        adopt=all(passed for passed, _ in checks),
        reasons=[f"{'pass' if passed else 'FAIL'}: {reason}" for passed, reason in checks],
    )
