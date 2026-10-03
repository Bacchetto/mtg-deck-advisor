# Retrieval comparison: `baseline` → `rrf-keyword-half`, 2026-10-02

Dev set (44 card queries, 45 rules questions). Baseline: As shipped in #70: cards hybrid, rules vector. Candidate: Hybrid with the keyword arm's fusion weight halved (0.5). Script: `scripts/retrieval_experiments.py`.

Deltas are in points (candidate minus baseline). The 95% intervals are bootstrap intervals over queries: with this few queries they're wide, and an interval that includes 0 means the change could be noise.

| Set | queries | recall@10 | MRR@10 | wins / losses / ties |
|---|---|---|---|---|
| Cards | 44 | 0.58 → 0.62 (+4.0, 95% [+0.6, +9.7]) | 0.45 → 0.50 (+5.3, 95% [+1.6, +10.0]) | 13 / 3 / 28 |
| Rules | 45 | 0.97 → 0.97 (+0.0, 95% [+0.0, +0.0]) | 0.70 → 0.70 (+0.0, 95% [+0.0, +0.0]) | 0 / 0 / 45 |

## Adoption rule

**Cards: adopt.** Candidate latency: median 91 ms, p90 322 ms.

- pass: best gain +5.3 points (needs +3)
- pass: 13 wins, 3 losses (needs more wins)
- pass: worst change +4.0 points (may drop at most 2)
- pass: median search 91 ms (budget 2 s)

**Rules: do not adopt.** Candidate latency: median 15 ms, p90 16 ms.

- FAIL: best gain +0.0 points (needs +3)
- FAIL: 0 wins, 0 losses (needs more wins)
- pass: worst change +0.0 points (may drop at most 2)
- pass: median search 15 ms (budget 2 s)

## By group (recall@10, MRR@10)

| Group | recall@10 | MRR@10 |
|---|---|---|
| cards, catalogue (23) | 0.59 → 0.64 | 0.42 → 0.48 |
| cards, `paraphrase` (18) | 0.47 → 0.54 | 0.32 → 0.37 |
| cards, `term` (3) | 1.00 → 1.00 | 0.61 → 0.83 |
| cards, `name` (2) | 1.00 → 1.00 | 1.00 → 1.00 |
| cards, pool (21) | 0.57 → 0.59 | 0.48 → 0.52 |
| cards, `need` (21) | 0.57 → 0.59 | 0.48 → 0.52 |
| rules, `commander` (14) | 0.93 → 0.93 | 0.59 → 0.59 |
| rules, `keyword` (16) | 0.97 → 0.97 | 0.70 → 0.70 |
| rules, `general` (15) | 1.00 → 1.00 | 0.81 → 0.81 |

## Queries that changed

| Query | | recall@10 | reciprocal rank |
|---|---|---|---|
| C02 one-mana white instant that exiles a creature, and its controller gains life equal to its power | win | 0.50 → 0.50 | 0.20 → 0.25 |
| C04 green sorcery that finds two basic lands, one onto the battlefield tapped and one into your hand | win | 0.25 → 0.25 | 0.25 → 0.33 |
| C10 an enchantment that doubles the tokens you create | win | 0.50 → 0.75 | 1.00 → 1.00 |
| C15 whenever an opponent draws a card, you get a Treasure unless they pay two | win | 0.00 → 1.00 | 0.00 → 0.11 |
| C16 bounce a nonland permanent you don't control, or overload it to bounce all of them | win | 1.00 → 1.00 | 0.10 → 0.14 |
| C17 a land that gives you no maximum hand size | win | 1.00 → 1.00 | 0.50 → 1.00 |
| C20 epic | win | 1.00 → 1.00 | 0.33 → 1.00 |
| P04 repeatable card draw | win | 0.33 → 0.33 | 0.17 → 0.33 |
| P07 get cards back from my graveyard to my hand | win | 0.22 → 0.22 | 0.33 → 0.50 |
| P08 make Treasure tokens | win | 1.00 → 1.00 | 0.50 → 1.00 |
| P09 give my creature hexproof, shroud or ward | win | 0.50 → 0.75 | 0.50 → 0.50 |
| P10 cheap burn to kill a creature | win | 0.25 → 0.50 | 0.10 → 0.20 |
| P13 lands that tap for two colors | loss | 1.00 → 1.00 | 0.33 → 0.25 |
| P16 stop opposing creatures from blocking | loss | 0.17 → 0.17 | 0.17 → 0.14 |
| P19 flicker a creature: exile it and return it to the battlefield | win | 1.00 → 1.00 | 0.25 → 0.33 |
| P21 copy my instant and sorcery spells | loss | 1.00 → 1.00 | 0.17 → 0.14 |
