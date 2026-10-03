# Retrieval comparison: `embed-8b` → `embed-8b-keyword-full`, 2026-10-02

Dev set (44 card queries, 45 rules questions). Baseline: qwen3-embedding:8b truncated to 1,024 dimensions; cards hybrid with the keyword arm at 0.5 (the current best), rules vector. Candidate: qwen3-embedding:8b, with the keyword arm at full weight (1.0). Script: `scripts/retrieval_experiments.py`.

Deltas are in points (candidate minus baseline). The 95% intervals are bootstrap intervals over queries: with this few queries they're wide, and an interval that includes 0 means the change could be noise.

| Set | queries | recall@10 | MRR@10 | wins / losses / ties |
|---|---|---|---|---|
| Cards | 44 | 0.72 → 0.69 (-3.3, 95% [-6.9, -0.1]) | 0.63 → 0.62 (-1.6, 95% [-5.8, +2.8]) | 4 / 14 / 26 |
| Rules | 45 | 1.00 → 1.00 (+0.0, 95% [+0.0, +0.0]) | 0.76 → 0.76 (+0.0, 95% [+0.0, +0.0]) | 0 / 0 / 45 |

## Adoption rule

**Cards: do not adopt.** Candidate latency: median 209 ms, p90 460 ms.

- FAIL: best gain -1.6 points (needs +3)
- FAIL: 4 wins, 14 losses (needs more wins)
- FAIL: worst change -3.3 points (may drop at most 2)
- pass: median search 209 ms (budget 2 s)

**Rules: do not adopt.** Candidate latency: median 54 ms, p90 66 ms.

- FAIL: best gain +0.0 points (needs +3)
- FAIL: 0 wins, 0 losses (needs more wins)
- pass: worst change +0.0 points (may drop at most 2)
- pass: median search 54 ms (budget 2 s)

## By group (recall@10, MRR@10)

| Group | recall@10 | MRR@10 |
|---|---|---|
| cards, catalogue (23) | 0.77 → 0.77 | 0.57 → 0.55 |
| cards, `paraphrase` (18) | 0.71 → 0.71 | 0.48 → 0.45 |
| cards, `term` (3) | 1.00 → 1.00 | 0.83 → 0.83 |
| cards, `name` (2) | 1.00 → 1.00 | 1.00 → 1.00 |
| cards, pool (21) | 0.67 → 0.60 | 0.70 → 0.69 |
| cards, `need` (21) | 0.67 → 0.60 | 0.70 → 0.69 |
| rules, `commander` (14) | 1.00 → 1.00 | 0.67 → 0.67 |
| rules, `keyword` (16) | 1.00 → 1.00 | 0.88 → 0.88 |
| rules, `general` (15) | 1.00 → 1.00 | 0.72 → 0.72 |

## Queries that changed

| Query | | recall@10 | reciprocal rank |
|---|---|---|---|
| C01 an artifact that costs one and taps for two colorless mana | loss | 1.00 → 1.00 | 0.50 → 0.33 |
| C02 one-mana white instant that exiles a creature, and its controller gains life equal to its power | loss | 0.50 → 0.50 | 0.50 → 0.33 |
| C04 green sorcery that finds two basic lands, one onto the battlefield tapped and one into your hand | win | 0.25 → 0.50 | 0.50 → 0.25 |
| C05 four-mana sorcery that destroys all creatures, and they can't be regenerated | loss | 1.00 → 1.00 | 0.50 → 0.33 |
| C10 an enchantment that doubles the tokens you create | loss | 1.00 → 0.75 | 1.00 → 1.00 |
| C13 equipment that gives a creature haste and stops it being targeted | loss | 1.00 → 1.00 | 0.33 → 0.25 |
| C15 whenever an opponent draws a card, you get a Treasure unless they pay two | loss | 1.00 → 1.00 | 0.17 → 0.11 |
| C16 bounce a nonland permanent you don't control, or overload it to bounce all of them | loss | 1.00 → 1.00 | 0.25 → 0.20 |
| C17 a land that gives you no maximum hand size | win | 1.00 → 1.00 | 0.50 → 1.00 |
| P04 repeatable card draw | loss | 0.33 → 0.33 | 0.20 → 0.17 |
| P05 board wipes that kill every creature | loss | 0.43 → 0.29 | 1.00 → 0.50 |
| P06 return a creature card from my graveyard to the battlefield | loss | 0.75 → 0.50 | 1.00 → 1.00 |
| P07 get cards back from my graveyard to my hand | win | 0.33 → 0.44 | 1.00 → 1.00 |
| P09 give my creature hexproof, shroud or ward | win | 0.50 → 0.50 | 0.50 → 1.00 |
| P10 cheap burn to kill a creature | loss | 0.75 → 0.50 | 0.33 → 0.17 |
| P13 lands that tap for two colors | loss | 1.00 → 0.75 | 1.00 → 1.00 |
| P16 stop opposing creatures from blocking | loss | 0.67 → 0.50 | 0.33 → 0.33 |
| P19 flicker a creature: exile it and return it to the battlefield | loss | 1.00 → 0.50 | 0.25 → 0.17 |
