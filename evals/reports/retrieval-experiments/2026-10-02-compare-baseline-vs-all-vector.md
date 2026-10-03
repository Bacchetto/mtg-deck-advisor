# Retrieval comparison: `baseline` → `all-vector`, 2026-10-02

Dev set (44 card queries, 45 rules questions). Baseline: As shipped in #70: cards hybrid, rules vector. Candidate: Vector search for cards and rules. Script: `scripts/retrieval_experiments.py`.

Deltas are in points (candidate minus baseline). The 95% intervals are bootstrap intervals over queries: with this few queries they're wide, and an interval that includes 0 means the change could be noise.

| Set | queries | recall@10 | MRR@10 | wins / losses / ties |
|---|---|---|---|---|
| Cards | 44 | 0.58 → 0.53 (-5.1, 95% [-13.9, +3.5]) | 0.45 → 0.49 (+4.7, 95% [-7.2, +16.6]) | 14 / 16 / 14 |
| Rules | 45 | 0.97 → 0.97 (+0.0, 95% [+0.0, +0.0]) | 0.70 → 0.70 (+0.0, 95% [+0.0, +0.0]) | 0 / 0 / 45 |

## Adoption rule

**Cards: do not adopt.** Candidate latency: median 88 ms, p90 94 ms.

- pass: best gain +4.7 points (needs +3)
- FAIL: 14 wins, 16 losses (needs more wins)
- FAIL: worst change -5.1 points (may drop at most 2)
- pass: median search 88 ms (budget 2 s)

**Rules: do not adopt.** Candidate latency: median 16 ms, p90 16 ms.

- FAIL: best gain +0.0 points (needs +3)
- FAIL: 0 wins, 0 losses (needs more wins)
- pass: worst change +0.0 points (may drop at most 2)
- pass: median search 16 ms (budget 2 s)

## By group (recall@10, MRR@10)

| Group | recall@10 | MRR@10 |
|---|---|---|
| cards, catalogue (23) | 0.59 → 0.56 | 0.42 → 0.51 |
| cards, `paraphrase` (18) | 0.47 → 0.46 | 0.32 → 0.42 |
| cards, `term` (3) | 1.00 → 0.87 | 0.61 → 0.83 |
| cards, `name` (2) | 1.00 → 1.00 | 1.00 → 0.75 |
| cards, pool (21) | 0.57 → 0.49 | 0.48 → 0.48 |
| cards, `need` (21) | 0.57 → 0.49 | 0.48 → 0.48 |
| rules, `commander` (14) | 0.93 → 0.93 | 0.59 → 0.59 |
| rules, `keyword` (16) | 0.97 → 0.97 | 0.70 → 0.70 |
| rules, `general` (15) | 1.00 → 1.00 | 0.81 → 0.81 |

## Queries that changed

| Query | | recall@10 | reciprocal rank |
|---|---|---|---|
| C02 one-mana white instant that exiles a creature, and its controller gains life equal to its power | win | 0.50 → 0.50 | 0.20 → 1.00 |
| C04 green sorcery that finds two basic lands, one onto the battlefield tapped and one into your hand | loss | 0.25 → 0.25 | 0.25 → 0.12 |
| C05 four-mana sorcery that destroys all creatures, and they can't be regenerated | loss | 0.50 → 0.00 | 0.33 → 0.00 |
| C08 equipment that draws two cards when the equipped creature dies | loss | 1.00 → 0.00 | 0.20 → 0.00 |
| C09 a land that taps for any color in your commander's color identity | loss | 0.75 → 0.50 | 1.00 → 1.00 |
| C10 an enchantment that doubles the tokens you create | win | 0.50 → 1.00 | 1.00 → 1.00 |
| C14 whenever an opponent casts a spell, you draw a card unless they pay one | win | 1.00 → 1.00 | 0.20 → 1.00 |
| C15 whenever an opponent draws a card, you get a Treasure unless they pay two | win | 0.00 → 1.00 | 0.00 → 0.25 |
| C16 bounce a nonland permanent you don't control, or overload it to bounce all of them | win | 1.00 → 1.00 | 0.10 → 0.25 |
| C17 a land that gives you no maximum hand size | win | 1.00 → 1.00 | 0.50 → 1.00 |
| C20 epic | loss | 1.00 → 0.60 | 0.33 → 1.00 |
| C23 kodamas reach | loss | 1.00 → 1.00 | 1.00 → 0.50 |
| P02 ways to destroy an artifact or enchantment | win | 0.25 → 0.25 | 0.25 → 1.00 |
| P04 repeatable card draw | win | 0.33 → 0.33 | 0.17 → 0.50 |
| P05 board wipes that kill every creature | loss | 0.14 → 0.14 | 1.00 → 0.50 |
| P06 return a creature card from my graveyard to the battlefield | win | 0.25 → 0.25 | 0.25 → 0.33 |
| P07 get cards back from my graveyard to my hand | loss | 0.22 → 0.11 | 0.33 → 0.12 |
| P08 make Treasure tokens | win | 1.00 → 1.00 | 0.50 → 1.00 |
| P09 give my creature hexproof, shroud or ward | win | 0.50 → 0.50 | 0.50 → 1.00 |
| P10 cheap burn to kill a creature | win | 0.25 → 0.50 | 0.10 → 0.50 |
| P12 tutor: search my library for a creature card | loss | 1.00 → 1.00 | 0.50 → 0.11 |
| P13 lands that tap for two colors | loss | 1.00 → 1.00 | 0.33 → 0.20 |
| P14 mana rocks that tap for any color | loss | 1.00 → 1.00 | 1.00 → 0.50 |
| P15 equipment that gives haste | loss | 1.00 → 0.50 | 1.00 → 0.33 |
| P16 stop opposing creatures from blocking | win | 0.17 → 0.33 | 0.17 → 1.00 |
| P17 mill an opponent | loss | 0.50 → 0.25 | 1.00 → 0.12 |
| P18 tap down opposing creatures | loss | 1.00 → 0.33 | 0.50 → 0.25 |
| P19 flicker a creature: exile it and return it to the battlefield | win | 1.00 → 1.00 | 0.25 → 1.00 |
| P20 take an extra turn | loss | 0.50 → 0.50 | 1.00 → 0.50 |
| P21 copy my instant and sorcery spells | loss | 1.00 → 0.50 | 0.17 → 0.11 |
