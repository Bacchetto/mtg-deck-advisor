# Retrieval comparison: `summary-vector` → `production`, 2026-10-04

Dev set (44 card queries, 45 rules questions). Baseline: Pilot: the summary embedded as its own vector and fused with the card vector and the keyword arm (weights 1, 1, 0.5); catalogue queries unchanged from embed-8b. Candidate: The app's own search_cards and search_rules, with their current defaults and the stored production embeddings: tracks what #76 ships. Script: `scripts/retrieval_experiments.py`.

Deltas are in points (candidate minus baseline). The 95% intervals are bootstrap intervals over queries: with this few queries they're wide, and an interval that includes 0 means the change could be noise.

| Set | queries | recall@10 | MRR@10 | wins / losses / ties |
|---|---|---|---|---|
| Cards | 44 | 0.78 → 0.77 (-0.4, 95% [-1.1, +0.0]) | 0.64 → 0.64 (+0.0, 95% [+0.0, +0.0]) | 0 / 1 / 43 |
| Rules | 45 | 1.00 → 1.00 (+0.0, 95% [+0.0, +0.0]) | 0.76 → 0.76 (+0.0, 95% [+0.0, +0.0]) | 0 / 0 / 45 |

## Adoption rule

**Cards: do not adopt.** Candidate latency: median 99 ms, p90 335 ms.

- FAIL: best gain +0.0 points (needs +3)
- FAIL: 0 wins, 1 losses (needs more wins)
- pass: worst change -0.4 points (may drop at most 2)
- pass: median search 99 ms (budget 2 s)

**Rules: do not adopt.** Candidate latency: median 38 ms, p90 50 ms.

- FAIL: best gain +0.0 points (needs +3)
- FAIL: 0 wins, 0 losses (needs more wins)
- pass: worst change +0.0 points (may drop at most 2)
- pass: median search 38 ms (budget 2 s)

## By group (recall@10, MRR@10)

| Group | recall@10 | MRR@10 |
|---|---|---|
| cards, catalogue (23) | 0.77 → 0.77 | 0.57 → 0.57 |
| cards, `paraphrase` (18) | 0.71 → 0.71 | 0.48 → 0.48 |
| cards, `term` (3) | 1.00 → 1.00 | 0.83 → 0.83 |
| cards, `name` (2) | 1.00 → 1.00 | 1.00 → 1.00 |
| cards, pool (21) | 0.78 → 0.77 | 0.73 → 0.73 |
| cards, `need` (21) | 0.78 → 0.77 | 0.73 → 0.73 |
| rules, `commander` (14) | 1.00 → 1.00 | 0.67 → 0.67 |
| rules, `keyword` (16) | 1.00 → 1.00 | 0.88 → 0.88 |
| rules, `general` (15) | 1.00 → 1.00 | 0.72 → 0.72 |

## Queries that changed

| Query | | recall@10 | reciprocal rank |
|---|---|---|---|
| P16 stop opposing creatures from blocking | loss | 1.00 → 0.83 | 1.00 → 1.00 |
