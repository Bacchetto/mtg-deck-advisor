# Retrieval comparison: `production` → `production-reranked`, 2026-10-04

Dev set (44 card queries, 45 rules questions). Baseline: The app's own search_cards and search_rules, with their current defaults and the stored production embeddings: tracks what #76 ships. Candidate: The app's own search_cards with its reranker (RERANK_MODEL), and search_rules: what #76 ships. Script: `scripts/retrieval_experiments.py`.

Deltas are in points (candidate minus baseline). The 95% intervals are bootstrap intervals over queries: with this few queries they're wide, and an interval that includes 0 means the change could be noise.

| Set | queries | recall@10 | MRR@10 | wins / losses / ties |
|---|---|---|---|---|
| Cards | 44 | 0.77 → 0.81 (+3.7, 95% [-2.3, +10.8]) | 0.64 → 0.83 (+18.7, 95% [+9.5, +28.2]) | 18 / 6 / 20 |
| Rules | 45 | 1.00 → 1.00 (+0.0, 95% [+0.0, +0.0]) | 0.76 → 0.76 (+0.0, 95% [+0.0, +0.0]) | 0 / 0 / 45 |

## Adoption rule

**Cards: adopt.** Candidate latency: median 1224 ms, p90 1614 ms.

- pass: best gain +18.7 points (needs +3)
- pass: 18 wins, 6 losses (needs more wins)
- pass: worst change +3.7 points (may drop at most 2)
- pass: median search 1224 ms (budget 2 s)

**Rules: do not adopt.** Candidate latency: median 38 ms, p90 43 ms.

- FAIL: best gain +0.0 points (needs +3)
- FAIL: 0 wins, 0 losses (needs more wins)
- pass: worst change +0.0 points (may drop at most 2)
- pass: median search 38 ms (budget 2 s)

## By group (recall@10, MRR@10)

| Group | recall@10 | MRR@10 |
|---|---|---|
| cards, catalogue (23) | 0.77 → 0.84 | 0.57 → 0.82 |
| cards, `paraphrase` (18) | 0.71 → 0.79 | 0.48 → 0.80 |
| cards, `term` (3) | 1.00 → 1.00 | 0.83 → 0.83 |
| cards, `name` (2) | 1.00 → 1.00 | 1.00 → 1.00 |
| cards, pool (21) | 0.77 → 0.78 | 0.73 → 0.84 |
| cards, `need` (21) | 0.77 → 0.78 | 0.73 → 0.84 |
| rules, `commander` (14) | 1.00 → 1.00 | 0.67 → 0.67 |
| rules, `keyword` (16) | 1.00 → 1.00 | 0.88 → 0.88 |
| rules, `general` (15) | 1.00 → 1.00 | 0.72 → 0.72 |

## Queries that changed

| Query | | recall@10 | reciprocal rank |
|---|---|---|---|
| C02 one-mana white instant that exiles a creature, and its controller gains life equal to its power | win | 0.50 → 0.50 | 0.50 → 1.00 |
| C04 green sorcery that finds two basic lands, one onto the battlefield tapped and one into your hand | win | 0.25 → 0.50 | 0.50 → 1.00 |
| C05 four-mana sorcery that destroys all creatures, and they can't be regenerated | win | 1.00 → 1.00 | 0.50 → 1.00 |
| C06 counter target spell for two blue mana | win | 0.00 → 1.00 | 0.00 → 0.14 |
| C07 black sorcery that searches your library for any card and puts it into your hand | win | 0.00 → 0.50 | 0.00 → 0.25 |
| C09 a land that taps for any color in your commander's color identity | loss | 1.00 → 0.75 | 1.00 → 1.00 |
| C11 sacrifice a creature to add colorless mana | win | 0.00 → 0.50 | 0.00 → 0.50 |
| C13 equipment that gives a creature haste and stops it being targeted | loss | 1.00 → 0.50 | 0.33 → 1.00 |
| C14 whenever an opponent casts a spell, you draw a card unless they pay one | win | 1.00 → 1.00 | 0.33 → 1.00 |
| C15 whenever an opponent draws a card, you get a Treasure unless they pay two | win | 1.00 → 1.00 | 0.17 → 1.00 |
| C16 bounce a nonland permanent you don't control, or overload it to bounce all of them | win | 1.00 → 1.00 | 0.25 → 1.00 |
| C17 a land that gives you no maximum hand size | win | 1.00 → 1.00 | 0.50 → 1.00 |
| C19 ripple | loss | 1.00 → 1.00 | 1.00 → 0.50 |
| C21 gravestorm | win | 1.00 → 1.00 | 0.50 → 1.00 |
| P03 cheap ramp | win | 0.33 → 0.33 | 0.20 → 0.25 |
| P04 repeatable card draw | win | 0.33 → 0.67 | 0.50 → 1.00 |
| P05 board wipes that kill every creature | loss | 0.71 → 0.57 | 1.00 → 1.00 |
| P06 return a creature card from my graveyard to the battlefield | loss | 0.75 → 0.50 | 1.00 → 0.50 |
| P07 get cards back from my graveyard to my hand | win | 0.33 → 0.44 | 0.25 → 1.00 |
| P10 cheap burn to kill a creature | win | 1.00 → 1.00 | 0.50 → 1.00 |
| P11 steal an opponent's creature | win | 0.00 → 0.33 | 0.00 → 0.10 |
| P13 lands that tap for two colors | loss | 1.00 → 0.75 | 0.33 → 0.33 |
| P18 tap down opposing creatures | win | 0.67 → 0.67 | 0.50 → 1.00 |
| P19 flicker a creature: exile it and return it to the battlefield | win | 1.00 → 1.00 | 0.50 → 1.00 |
