# 0011 - Retrieval improvements, chosen on a dev set and checked on a held-out set

**Status:** Accepted, 2026-10-04
**Applies to:** `mtg_deck_advisor.retrieval` (`search`, `rerank`, `summaries`, `embeddings`), `llm.ollama.OllamaEmbedder`, settings `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`, `SUMMARY_MODEL`, `RERANK_MODEL`

## Context

The first retrieval eval (#64) left cards weak:
- recall@10 was 0.58 and MRR@10 0.45 on 44 labelled dev queries
- cards described in other words scored 0.47
- deck-building needs over a pool scored 0.57

The owner chose to improve retrieval before closing Milestone 4. The budget was a median of 2 s per search, local and free. The risk of tuning is fooling yourself: picking whatever scores best on the same small set you report on.

## Decision

**How decisions were made:**
1. **A held-out test set, written before any tuning.** It has 41 card queries, 22 rules questions and its own 300-card pool, checked by code to share nothing with the dev set (#71). It was scored twice: once for the baseline and once for the final configuration. Never for a decision.
2. **An adoption rule, fixed before any experiment.** A variant replaces the current best only if, on dev, it meets all four conditions:
   - recall@10 or MRR improves by at least 3 points
   - it wins more queries than it loses
   - neither metric drops by more than 2 points
   - the median search stays within 2 s
3. **Paired comparisons with bootstrap intervals** (#72), so noise shows up as an interval spanning zero.

**What was adopted** (every comparison is in `evals/reports/retrieval-experiments/`):

| Lever | Dev result against the best before it | Where |
|---|---|---|
| Keyword arm at half weight in the fusion | cards +4.0 / +5.3, 13 wins / 3 losses | #73 |
| `qwen3-embedding:8b`, truncated to 1,024 dimensions | cards 0.62 → 0.72 / 0.50 → 0.63; rules MRR 0.70 → 0.76 | #73, ADR 0009 |
| One-sentence summaries (local `qwen3:14b`), as a third vector, **for pool searches only** | cards recall@10 +5.3, 10 wins / 3 losses | #74 |
| `qwen3:8b` reranking the top 20 cards | cards MRR 0.64 → 0.83 (+18.7), median 1.3 s | #75 |

**What was rejected:**
- Other query instructions: no gain. With no instruction at all, cards lost 8 points and rules 11.
- Fusion depth 30 or 100: "gains" in both directions, which is noise.
- A keyword arm that ranks all-words matches first.
- Summaries appended to the card text, or used for every search: they hurt name searches.
- A `qwen3:14b` reranker: it took 9 s, because beside the 8b embedder the overflow spills into system RAM.
- A `qwen3:4b` reranker for cards.
- HyDE: worse and slower.
- Reranking rules: the 4b helped, but a second chat model would spill VRAM, and rules already find the answer in the top 10.

## Result on the held-out set

| Held-out | Baseline | Final |
|---|---|---|
| Cards (41): recall@10 / MRR@10 | 0.63 / 0.54 | **0.72 / 0.79** |
| Rules (22): recall@10 / MRR@10 | 1.00 / 0.67 | **1.00 / 0.75** |
| Median card search | 24 ms | 1.13 s |

On dev the same configuration went from 0.58 / 0.45 to 0.83 / 0.81. The difference is the optimism of choosing on dev, and the held-out numbers are the ones to quote.

## Consequences

- **Search needs about 12 GB of VRAM:** the 8b embedder (6.6 GB) plus the `qwen3:8b` reranker (5.6 GB). On a smaller GPU, reranking can be turned off (`RERANK_MODEL=`), and search falls back to the first stage on any reranker failure. If anything else is loaded beside them, reranking slows about 6 times.
- **Card searches take about a second** (almost all reranking). Rules searches take about 30 ms.
- **Summaries are a retrieval signal only.** They can be wrong: a vanilla creature was summarised with haste. Nothing downstream treats them as card text. New or changed cards are summarised by `embed summaries`, at about 0.75 s each.
- **Known regression: single-word mechanic searches** ("recover", "transfigure") fell from 0.80 to 0.30 recall@10 on the held-out set, because the half-weight keyword arm and the reranker both work against exact names. Any fix (for example, skipping the reranker for a bare keyword-ability query) must be measured on dev, not on these numbers.
- **The reproducibility floor:** GPU embedding isn't bit-for-bit repeatable across different batch layouts (cosine ≥ 0.9967 between runs). Near-equal neighbours can swap, so differences of a query or two between runs aren't meaningful.
- **The held-out set is now spent** for this configuration. A future round of tuning needs a fresh held-out set, or must report that this one has been seen twice.
