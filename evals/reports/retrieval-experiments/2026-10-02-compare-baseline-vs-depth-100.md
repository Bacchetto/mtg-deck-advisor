# Retrieval comparison: `baseline` → `depth-100`, 2026-10-02

Dev set (44 card queries, 45 rules questions). Baseline: As shipped in #70: cards hybrid, rules vector. Candidate: Hybrid fusing each arm's top 100 (baseline: 50). Script: `scripts/retrieval_experiments.py`.

Deltas are in points (candidate minus baseline). The 95% intervals are bootstrap intervals over queries: with this few queries they're wide, and an interval that includes 0 means the change could be noise.

| Set | queries | recall@10 | MRR@10 | wins / losses / ties |
|---|---|---|---|---|
| Cards | 44 | 0.58 → 0.60 (+2.3, 95% [+0.0, +5.1]) | 0.45 → 0.45 (+0.9, 95% [+0.1, +1.8]) | 6 / 0 / 38 |
| Rules | 45 | 0.97 → 0.97 (+0.0, 95% [+0.0, +0.0]) | 0.70 → 0.70 (+0.0, 95% [+0.0, +0.0]) | 0 / 0 / 45 |

## Adoption rule

**Cards: do not adopt.** Candidate latency: median 91 ms, p90 337 ms.

- FAIL: best gain +2.3 points (needs +3)
- pass: 6 wins, 0 losses (needs more wins)
- pass: worst change +0.9 points (may drop at most 2)
- pass: median search 91 ms (budget 2 s)

**Rules: do not adopt.** Candidate latency: median 15 ms, p90 16 ms.

- FAIL: best gain +0.0 points (needs +3)
- FAIL: 0 wins, 0 losses (needs more wins)
- pass: worst change +0.0 points (may drop at most 2)
- pass: median search 15 ms (budget 2 s)

## By group (recall@10, MRR@10)

| Group | recall@10 | MRR@10 |
|---|---|---|
| cards, catalogue (23) | 0.59 → 0.62 | 0.42 → 0.43 |
| cards, `paraphrase` (18) | 0.47 → 0.51 | 0.32 → 0.34 |
| cards, `term` (3) | 1.00 → 1.00 | 0.61 → 0.61 |
| cards, `name` (2) | 1.00 → 1.00 | 1.00 → 1.00 |
| cards, pool (21) | 0.57 → 0.58 | 0.48 → 0.48 |
| cards, `need` (21) | 0.57 → 0.58 | 0.48 → 0.48 |
| rules, `commander` (14) | 0.93 → 0.93 | 0.59 → 0.59 |
| rules, `keyword` (16) | 0.97 → 0.97 | 0.70 → 0.70 |
| rules, `general` (15) | 1.00 → 1.00 | 0.81 → 0.81 |

## Queries that changed

| Query | | recall@10 | reciprocal rank |
|---|---|---|---|
| C02 one-mana white instant that exiles a creature, and its controller gains life equal to its power | win | 0.50 → 0.50 | 0.20 → 0.25 |
| C07 black sorcery that searches your library for any card and puts it into your hand | win | 0.00 → 0.50 | 0.00 → 0.12 |
| C10 an enchantment that doubles the tokens you create | win | 0.50 → 0.75 | 1.00 → 1.00 |
| C14 whenever an opponent casts a spell, you draw a card unless they pay one | win | 1.00 → 1.00 | 0.20 → 0.25 |
| C16 bounce a nonland permanent you don't control, or overload it to bounce all of them | win | 1.00 → 1.00 | 0.10 → 0.25 |
| P06 return a creature card from my graveyard to the battlefield | win | 0.25 → 0.50 | 0.25 → 0.25 |
