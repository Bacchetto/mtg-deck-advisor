# Retrieval comparison: `rrf-keyword-half` → `keyword-half-depth-100`, 2026-10-02

Dev set (44 card queries, 45 rules questions). Baseline: Hybrid with the keyword arm's fusion weight halved (0.5). Candidate: The keyword arm at weight 0.5, fusing each arm's top 100. Script: `scripts/retrieval_experiments.py`.

Deltas are in points (candidate minus baseline). The 95% intervals are bootstrap intervals over queries: with this few queries they're wide, and an interval that includes 0 means the change could be noise.

| Set | queries | recall@10 | MRR@10 | wins / losses / ties |
|---|---|---|---|---|
| Cards | 44 | 0.62 → 0.59 (-2.3, 95% [-6.8, +0.0]) | 0.50 → 0.51 (+1.5, 95% [-0.6, +4.3]) | 3 / 3 / 38 |
| Rules | 45 | 0.97 → 0.97 (+0.0, 95% [+0.0, +0.0]) | 0.70 → 0.70 (+0.0, 95% [+0.0, +0.0]) | 0 / 0 / 45 |

## Adoption rule

**Cards: do not adopt.** Candidate latency: median 92 ms, p90 375 ms.

- FAIL: best gain +1.5 points (needs +3)
- FAIL: 3 wins, 3 losses (needs more wins)
- FAIL: worst change -2.3 points (may drop at most 2)
- pass: median search 92 ms (budget 2 s)

**Rules: do not adopt.** Candidate latency: median 15 ms, p90 16 ms.

- FAIL: best gain +0.0 points (needs +3)
- FAIL: 0 wins, 0 losses (needs more wins)
- pass: worst change +0.0 points (may drop at most 2)
- pass: median search 15 ms (budget 2 s)

## By group (recall@10, MRR@10)

| Group | recall@10 | MRR@10 |
|---|---|---|
| cards, catalogue (23) | 0.64 → 0.60 | 0.48 → 0.51 |
| cards, `paraphrase` (18) | 0.54 → 0.49 | 0.37 → 0.40 |
| cards, `term` (3) | 1.00 → 1.00 | 0.83 → 0.83 |
| cards, `name` (2) | 1.00 → 1.00 | 1.00 → 1.00 |
| cards, pool (21) | 0.59 → 0.59 | 0.52 → 0.52 |
| cards, `need` (21) | 0.59 → 0.59 | 0.52 → 0.52 |
| rules, `commander` (14) | 0.93 → 0.93 | 0.59 → 0.59 |
| rules, `keyword` (16) | 0.97 → 0.97 | 0.70 → 0.70 |
| rules, `general` (15) | 1.00 → 1.00 | 0.81 → 0.81 |

## Queries that changed

| Query | | recall@10 | reciprocal rank |
|---|---|---|---|
| C02 one-mana white instant that exiles a creature, and its controller gains life equal to its power | win | 0.50 → 0.50 | 0.25 → 0.50 |
| C05 four-mana sorcery that destroys all creatures, and they can't be regenerated | loss | 0.50 → 0.50 | 0.33 → 0.25 |
| C08 equipment that draws two cards when the equipped creature dies | loss | 1.00 → 1.00 | 0.20 → 0.17 |
| C14 whenever an opponent casts a spell, you draw a card unless they pay one | win | 1.00 → 1.00 | 0.20 → 0.50 |
| C15 whenever an opponent draws a card, you get a Treasure unless they pay two | loss | 1.00 → 0.00 | 0.11 → 0.00 |
| C16 bounce a nonland permanent you don't control, or overload it to bounce all of them | win | 1.00 → 1.00 | 0.14 → 0.50 |
