# Retrieval comparison: `baseline` → `depth-30`, 2026-10-02

Dev set (44 card queries, 45 rules questions). Baseline: As shipped in #70: cards hybrid, rules vector. Candidate: Hybrid fusing each arm's top 30 (baseline: 50). Script: `scripts/retrieval_experiments.py`.

Deltas are in points (candidate minus baseline). The 95% intervals are bootstrap intervals over queries: with this few queries they're wide, and an interval that includes 0 means the change could be noise.

| Set | queries | recall@10 | MRR@10 | wins / losses / ties |
|---|---|---|---|---|
| Cards | 44 | 0.58 → 0.61 (+3.1, 95% [-1.8, +9.1]) | 0.45 → 0.45 (+0.0, 95% [-2.1, +2.2]) | 9 / 2 / 33 |
| Rules | 45 | 0.97 → 0.97 (+0.0, 95% [+0.0, +0.0]) | 0.70 → 0.70 (+0.0, 95% [+0.0, +0.0]) | 0 / 0 / 45 |

## Adoption rule

**Cards: adopt.** Candidate latency: median 90 ms, p90 347 ms.

- pass: best gain +3.1 points (needs +3)
- pass: 9 wins, 2 losses (needs more wins)
- pass: worst change +0.0 points (may drop at most 2)
- pass: median search 90 ms (budget 2 s)

**Rules: do not adopt.** Candidate latency: median 15 ms, p90 16 ms.

- FAIL: best gain +0.0 points (needs +3)
- FAIL: 0 wins, 0 losses (needs more wins)
- pass: worst change +0.0 points (may drop at most 2)
- pass: median search 15 ms (budget 2 s)

## By group (recall@10, MRR@10)

| Group | recall@10 | MRR@10 |
|---|---|---|
| cards, catalogue (23) | 0.59 → 0.61 | 0.42 → 0.42 |
| cards, `paraphrase` (18) | 0.47 → 0.50 | 0.32 → 0.33 |
| cards, `term` (3) | 1.00 → 1.00 | 0.61 → 0.61 |
| cards, `name` (2) | 1.00 → 1.00 | 1.00 → 1.00 |
| cards, pool (21) | 0.57 → 0.61 | 0.48 → 0.47 |
| cards, `need` (21) | 0.57 → 0.61 | 0.48 → 0.47 |
| rules, `commander` (14) | 0.93 → 0.93 | 0.59 → 0.59 |
| rules, `keyword` (16) | 0.97 → 0.97 | 0.70 → 0.70 |
| rules, `general` (15) | 1.00 → 1.00 | 0.81 → 0.81 |

## Queries that changed

| Query | | recall@10 | reciprocal rank |
|---|---|---|---|
| C02 one-mana white instant that exiles a creature, and its controller gains life equal to its power | win | 0.50 → 0.50 | 0.20 → 0.25 |
| C05 four-mana sorcery that destroys all creatures, and they can't be regenerated | loss | 0.50 → 0.00 | 0.33 → 0.00 |
| C09 a land that taps for any color in your commander's color identity | loss | 0.75 → 0.50 | 1.00 → 1.00 |
| C10 an enchantment that doubles the tokens you create | win | 0.50 → 0.75 | 1.00 → 1.00 |
| C14 whenever an opponent casts a spell, you draw a card unless they pay one | win | 1.00 → 1.00 | 0.20 → 0.50 |
| C15 whenever an opponent draws a card, you get a Treasure unless they pay two | win | 0.00 → 1.00 | 0.00 → 0.10 |
| C16 bounce a nonland permanent you don't control, or overload it to bounce all of them | win | 1.00 → 1.00 | 0.10 → 0.12 |
| P06 return a creature card from my graveyard to the battlefield | win | 0.25 → 0.50 | 0.25 → 0.12 |
| P07 get cards back from my graveyard to my hand | win | 0.22 → 0.33 | 0.33 → 0.33 |
| P09 give my creature hexproof, shroud or ward | win | 0.50 → 0.75 | 0.50 → 0.50 |
| P17 mill an opponent | win | 0.50 → 0.75 | 1.00 → 1.00 |
