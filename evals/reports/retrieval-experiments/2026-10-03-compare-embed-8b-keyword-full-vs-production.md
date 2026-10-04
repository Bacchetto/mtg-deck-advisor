# Retrieval comparison: `embed-8b-keyword-full` → `production`, 2026-10-03

Dev set (44 card queries, 45 rules questions). Baseline: qwen3-embedding:8b, with the keyword arm at full weight (1.0). Candidate: The app's own search_cards and search_rules, with their current defaults and the stored production embeddings: tracks what #76 ships. Script: `scripts/retrieval_experiments.py`.

Deltas are in points (candidate minus baseline). The 95% intervals are bootstrap intervals over queries: with this few queries they're wide, and an interval that includes 0 means the change could be noise.

| Set | queries | recall@10 | MRR@10 | wins / losses / ties |
|---|---|---|---|---|
| Cards | 44 | 0.69 → 0.69 (+0.0, 95% [+0.0, +0.0]) | 0.62 → 0.62 (+0.0, 95% [+0.0, +0.0]) | 0 / 0 / 44 |
| Rules | 45 | 1.00 → 1.00 (+0.0, 95% [+0.0, +0.0]) | 0.76 → 0.76 (+0.0, 95% [+0.0, +0.0]) | 0 / 0 / 45 |

## Adoption rule

**Cards: do not adopt.** Candidate latency: median 120 ms, p90 396 ms.

- FAIL: best gain +0.0 points (needs +3)
- FAIL: 0 wins, 0 losses (needs more wins)
- pass: worst change +0.0 points (may drop at most 2)
- pass: median search 120 ms (budget 2 s)

**Rules: do not adopt.** Candidate latency: median 36 ms, p90 40 ms.

- FAIL: best gain +0.0 points (needs +3)
- FAIL: 0 wins, 0 losses (needs more wins)
- pass: worst change +0.0 points (may drop at most 2)
- pass: median search 36 ms (budget 2 s)

## By group (recall@10, MRR@10)

| Group | recall@10 | MRR@10 |
|---|---|---|
| cards, catalogue (23) | 0.77 → 0.77 | 0.55 → 0.55 |
| cards, `paraphrase` (18) | 0.71 → 0.71 | 0.45 → 0.45 |
| cards, `term` (3) | 1.00 → 1.00 | 0.83 → 0.83 |
| cards, `name` (2) | 1.00 → 1.00 | 1.00 → 1.00 |
| cards, pool (21) | 0.60 → 0.60 | 0.69 → 0.69 |
| cards, `need` (21) | 0.60 → 0.60 | 0.69 → 0.69 |
| rules, `commander` (14) | 1.00 → 1.00 | 0.67 → 0.67 |
| rules, `keyword` (16) | 1.00 → 1.00 | 0.88 → 0.88 |
| rules, `general` (15) | 1.00 → 1.00 | 0.72 → 0.72 |

## Queries that changed

| Query | | recall@10 | reciprocal rank |
|---|---|---|---|
