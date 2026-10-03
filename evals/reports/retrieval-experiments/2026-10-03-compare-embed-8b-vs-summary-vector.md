# Retrieval comparison: `embed-8b` → `summary-vector`, 2026-10-03

Dev set (44 card queries, 45 rules questions). Baseline: qwen3-embedding:8b truncated to 1,024 dimensions; cards hybrid with the keyword arm at 0.5 (the current best), rules vector. Candidate: Pilot: the summary embedded as its own vector and fused with the card vector and the keyword arm (weights 1, 1, 0.5); catalogue queries unchanged from embed-8b. Script: `scripts/retrieval_experiments.py`.

Deltas are in points (candidate minus baseline). The 95% intervals are bootstrap intervals over queries: with this few queries they're wide, and an interval that includes 0 means the change could be noise.

| Set | queries | recall@10 | MRR@10 | wins / losses / ties |
|---|---|---|---|---|
| Cards | 44 | 0.72 → 0.78 (+5.3, 95% [+1.8, +9.4]) | 0.63 → 0.64 (+1.1, 95% [-5.4, +7.0]) | 10 / 3 / 31 |
| Rules | 45 | 1.00 → 1.00 (+0.0, 95% [+0.0, +0.0]) | 0.76 → 0.76 (+0.0, 95% [+0.0, +0.0]) | 0 / 0 / 45 |

## Adoption rule

**Cards: adopt.** Candidate latency: median 149 ms, p90 438 ms.

- pass: best gain +5.3 points (needs +3)
- pass: 10 wins, 3 losses (needs more wins)
- pass: worst change +1.1 points (may drop at most 2)
- pass: median search 149 ms (budget 2 s)

**Rules: do not adopt.** Candidate latency: median 50 ms, p90 59 ms.

- FAIL: best gain +0.0 points (needs +3)
- FAIL: 0 wins, 0 losses (needs more wins)
- pass: worst change +0.0 points (may drop at most 2)
- pass: median search 50 ms (budget 2 s)

## By group (recall@10, MRR@10)

| Group | recall@10 | MRR@10 |
|---|---|---|
| cards, catalogue (23) | 0.77 → 0.77 | 0.57 → 0.57 |
| cards, `paraphrase` (18) | 0.71 → 0.71 | 0.48 → 0.48 |
| cards, `term` (3) | 1.00 → 1.00 | 0.83 → 0.83 |
| cards, `name` (2) | 1.00 → 1.00 | 1.00 → 1.00 |
| cards, pool (21) | 0.67 → 0.78 | 0.70 → 0.73 |
| cards, `need` (21) | 0.67 → 0.78 | 0.70 → 0.73 |
| rules, `commander` (14) | 1.00 → 1.00 | 0.67 → 0.67 |
| rules, `keyword` (16) | 1.00 → 1.00 | 0.88 → 0.88 |
| rules, `general` (15) | 1.00 → 1.00 | 0.72 → 0.72 |

## Queries that changed

| Query | | recall@10 | reciprocal rank |
|---|---|---|---|
| P01 counterspells | win | 0.80 → 1.00 | 1.00 → 1.00 |
| P03 cheap ramp | loss | 0.33 → 0.33 | 0.33 → 0.20 |
| P04 repeatable card draw | win | 0.33 → 0.33 | 0.20 → 0.50 |
| P05 board wipes that kill every creature | win | 0.43 → 0.71 | 1.00 → 1.00 |
| P07 get cards back from my graveyard to my hand | loss | 0.33 → 0.33 | 1.00 → 0.25 |
| P09 give my creature hexproof, shroud or ward | win | 0.50 → 1.00 | 0.50 → 1.00 |
| P10 cheap burn to kill a creature | win | 0.75 → 1.00 | 0.33 → 0.50 |
| P13 lands that tap for two colors | loss | 1.00 → 1.00 | 1.00 → 0.33 |
| P16 stop opposing creatures from blocking | win | 0.67 → 1.00 | 0.33 → 1.00 |
| P17 mill an opponent | win | 0.75 → 1.00 | 1.00 → 1.00 |
| P18 tap down opposing creatures | win | 0.67 → 0.67 | 0.33 → 0.50 |
| P19 flicker a creature: exile it and return it to the battlefield | win | 1.00 → 1.00 | 0.25 → 0.50 |
| P20 take an extra turn | win | 0.50 → 1.00 | 1.00 → 1.00 |
