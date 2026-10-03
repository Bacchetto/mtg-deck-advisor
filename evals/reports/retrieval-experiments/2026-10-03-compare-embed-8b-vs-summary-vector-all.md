# Retrieval comparison: `embed-8b` → `summary-vector-all`, 2026-10-03

Dev set (44 card queries, 45 rules questions). Baseline: qwen3-embedding:8b truncated to 1,024 dimensions; cards hybrid with the keyword arm at 0.5 (the current best), rules vector. Candidate: Every card summarised: the summary embedded as its own vector and fused with the card vector and the keyword arm (weights 1, 1, 0.5), for all card queries. Script: `scripts/retrieval_experiments.py`.

Deltas are in points (candidate minus baseline). The 95% intervals are bootstrap intervals over queries: with this few queries they're wide, and an interval that includes 0 means the change could be noise.

| Set | queries | recall@10 | MRR@10 | wins / losses / ties |
|---|---|---|---|---|
| Cards | 44 | 0.72 → 0.75 (+2.4, 95% [-5.0, +9.1]) | 0.63 → 0.65 (+2.1, 95% [-8.5, +12.3]) | 16 / 10 / 18 |
| Rules | 45 | 1.00 → 1.00 (+0.0, 95% [+0.0, +0.0]) | 0.76 → 0.76 (+0.0, 95% [+0.0, +0.0]) | 0 / 0 / 45 |

## Adoption rule

**Cards: do not adopt.** Candidate latency: median 334 ms, p90 776 ms.

- FAIL: best gain +2.4 points (needs +3)
- pass: 16 wins, 10 losses (needs more wins)
- pass: worst change +2.1 points (may drop at most 2)
- pass: median search 334 ms (budget 2 s)

**Rules: do not adopt.** Candidate latency: median 50 ms, p90 62 ms.

- FAIL: best gain +0.0 points (needs +3)
- FAIL: 0 wins, 0 losses (needs more wins)
- pass: worst change +0.0 points (may drop at most 2)
- pass: median search 50 ms (budget 2 s)

## By group (recall@10, MRR@10)

| Group | recall@10 | MRR@10 |
|---|---|---|
| cards, catalogue (23) | 0.77 → 0.72 | 0.57 → 0.59 |
| cards, `paraphrase` (18) | 0.71 → 0.64 | 0.48 → 0.59 |
| cards, `term` (3) | 1.00 → 1.00 | 0.83 → 0.75 |
| cards, `name` (2) | 1.00 → 1.00 | 1.00 → 0.31 |
| cards, pool (21) | 0.67 → 0.78 | 0.70 → 0.73 |
| cards, `need` (21) | 0.67 → 0.78 | 0.70 → 0.73 |
| rules, `commander` (14) | 1.00 → 1.00 | 0.67 → 0.67 |
| rules, `keyword` (16) | 1.00 → 1.00 | 0.88 → 0.88 |
| rules, `general` (15) | 1.00 → 1.00 | 0.72 → 0.72 |

## Queries that changed

| Query | | recall@10 | reciprocal rank |
|---|---|---|---|
| C01 an artifact that costs one and taps for two colorless mana | loss | 1.00 → 0.00 | 0.50 → 0.00 |
| C04 green sorcery that finds two basic lands, one onto the battlefield tapped and one into your hand | win | 0.25 → 0.50 | 0.50 → 1.00 |
| C09 a land that taps for any color in your commander's color identity | loss | 1.00 → 0.75 | 1.00 → 0.33 |
| C10 an enchantment that doubles the tokens you create | loss | 1.00 → 0.75 | 1.00 → 1.00 |
| C11 sacrifice a creature to add colorless mana | win | 0.00 → 0.50 | 0.00 → 0.33 |
| C13 equipment that gives a creature haste and stops it being targeted | loss | 1.00 → 0.50 | 0.33 → 0.50 |
| C14 whenever an opponent casts a spell, you draw a card unless they pay one | win | 1.00 → 1.00 | 0.33 → 1.00 |
| C15 whenever an opponent draws a card, you get a Treasure unless they pay two | win | 1.00 → 1.00 | 0.17 → 0.50 |
| C16 bounce a nonland permanent you don't control, or overload it to bounce all of them | win | 1.00 → 1.00 | 0.25 → 1.00 |
| C17 a land that gives you no maximum hand size | win | 1.00 → 1.00 | 0.50 → 1.00 |
| C21 gravestorm | loss | 1.00 → 1.00 | 0.50 → 0.25 |
| C22 Atraxa | loss | 1.00 → 1.00 | 1.00 → 0.50 |
| C23 kodamas reach | loss | 1.00 → 1.00 | 1.00 → 0.11 |
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
