# Retrieval comparison: `embed-8b` → `summary-appended`, 2026-10-02

Dev set (21 card queries, 45 rules questions). Baseline: qwen3-embedding:8b truncated to 1,024 dimensions; cards hybrid with the keyword arm at 0.5 (the current best), rules vector. Candidate: Pilot: pool cards embedded (8b) with a one-sentence summary by qwen3:14b appended; catalogue queries unchanged from embed-8b. Script: `scripts/retrieval_experiments.py`.

Deltas are in points (candidate minus baseline). The 95% intervals are bootstrap intervals over queries: with this few queries they're wide, and an interval that includes 0 means the change could be noise.

| Set | queries | recall@10 | MRR@10 | wins / losses / ties |
|---|---|---|---|---|
| Cards | 21 | 0.67 → 0.66 (-1.0, 95% [-6.4, +6.0]) | 0.70 → 0.70 (-0.6, 95% [-11.3, +9.5]) | 5 / 7 / 9 |
| Rules | 45 | 1.00 → 1.00 (+0.0, 95% [+0.0, +0.0]) | 0.76 → 0.76 (+0.0, 95% [+0.0, +0.0]) | 0 / 0 / 45 |

## Adoption rule

**Cards: do not adopt.** Candidate latency: median 126 ms, p90 154 ms.

- FAIL: best gain -0.6 points (needs +3)
- FAIL: 5 wins, 7 losses (needs more wins)
- pass: worst change -1.0 points (may drop at most 2)
- pass: median search 126 ms (budget 2 s)

**Rules: do not adopt.** Candidate latency: median 56 ms, p90 71 ms.

- FAIL: best gain +0.0 points (needs +3)
- FAIL: 0 wins, 0 losses (needs more wins)
- pass: worst change +0.0 points (may drop at most 2)
- pass: median search 56 ms (budget 2 s)

## By group (recall@10, MRR@10)

| Group | recall@10 | MRR@10 |
|---|---|---|
| cards, pool (21) | 0.67 → 0.66 | 0.70 → 0.70 |
| cards, `need` (21) | 0.67 → 0.66 | 0.70 → 0.70 |
| rules, `commander` (14) | 1.00 → 1.00 | 0.67 → 0.67 |
| rules, `keyword` (16) | 1.00 → 1.00 | 0.88 → 0.88 |
| rules, `general` (15) | 1.00 → 1.00 | 0.72 → 0.72 |

## Queries that changed

| Query | | recall@10 | reciprocal rank |
|---|---|---|---|
| P01 counterspells | loss | 0.80 → 0.60 | 1.00 → 1.00 |
| P03 cheap ramp | loss | 0.33 → 0.33 | 0.33 → 0.25 |
| P04 repeatable card draw | loss | 0.33 → 0.33 | 0.20 → 0.17 |
| P06 return a creature card from my graveyard to the battlefield | loss | 0.75 → 0.75 | 1.00 → 0.50 |
| P09 give my creature hexproof, shroud or ward | win | 0.50 → 0.50 | 0.50 → 1.00 |
| P10 cheap burn to kill a creature | loss | 0.75 → 0.50 | 0.33 → 0.25 |
| P12 tutor: search my library for a creature card | loss | 1.00 → 1.00 | 1.00 → 0.33 |
| P13 lands that tap for two colors | loss | 1.00 → 0.75 | 1.00 → 1.00 |
| P16 stop opposing creatures from blocking | win | 0.67 → 0.67 | 0.33 → 0.50 |
| P19 flicker a creature: exile it and return it to the battlefield | win | 1.00 → 1.00 | 0.25 → 0.33 |
| P20 take an extra turn | win | 0.50 → 1.00 | 1.00 → 1.00 |
| P21 copy my instant and sorcery spells | win | 1.00 → 1.00 | 0.50 → 1.00 |
