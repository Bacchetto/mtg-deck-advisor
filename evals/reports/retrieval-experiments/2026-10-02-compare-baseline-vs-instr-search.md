# Retrieval comparison: `baseline` → `instr-search`, 2026-10-02

Dev set (44 card queries, 45 rules questions). Baseline: As shipped in #70: cards hybrid, rules vector. Candidate: Card query instruction: 'Given a search for Magic: The Gathering cards by name, mechanic or effect, retrieve the cards that match'; rules: 'Given a question about playing Magic: The Gathering, retrieve the Comprehensive Rules passage that answers it'. Script: `scripts/retrieval_experiments.py`.

Deltas are in points (candidate minus baseline). The 95% intervals are bootstrap intervals over queries: with this few queries they're wide, and an interval that includes 0 means the change could be noise.

| Set | queries | recall@10 | MRR@10 | wins / losses / ties |
|---|---|---|---|---|
| Cards | 44 | 0.58 → 0.56 (-1.5, 95% [-9.7, +6.2]) | 0.45 → 0.46 (+1.1, 95% [-3.8, +5.9]) | 8 / 7 / 29 |
| Rules | 45 | 0.97 → 0.97 (+0.0, 95% [+0.0, +0.0]) | 0.70 → 0.68 (-2.8, 95% [-7.4, +1.3]) | 2 / 7 / 36 |

## Adoption rule

**Cards: do not adopt.** Candidate latency: median 90 ms, p90 321 ms.

- FAIL: best gain +1.1 points (needs +3)
- pass: 8 wins, 7 losses (needs more wins)
- pass: worst change -1.5 points (may drop at most 2)
- pass: median search 90 ms (budget 2 s)

**Rules: do not adopt.** Candidate latency: median 18 ms, p90 23 ms.

- FAIL: best gain +0.0 points (needs +3)
- FAIL: 2 wins, 7 losses (needs more wins)
- FAIL: worst change -2.8 points (may drop at most 2)
- pass: median search 18 ms (budget 2 s)

## By group (recall@10, MRR@10)

| Group | recall@10 | MRR@10 |
|---|---|---|
| cards, catalogue (23) | 0.59 → 0.60 | 0.42 → 0.45 |
| cards, `paraphrase` (18) | 0.47 → 0.49 | 0.32 → 0.36 |
| cards, `term` (3) | 1.00 → 1.00 | 0.61 → 0.61 |
| cards, `name` (2) | 1.00 → 1.00 | 1.00 → 1.00 |
| cards, pool (21) | 0.57 → 0.53 | 0.48 → 0.47 |
| cards, `need` (21) | 0.57 → 0.53 | 0.48 → 0.47 |
| rules, `commander` (14) | 0.93 → 0.93 | 0.59 → 0.57 |
| rules, `keyword` (16) | 0.97 → 0.97 | 0.70 → 0.72 |
| rules, `general` (15) | 1.00 → 1.00 | 0.81 → 0.73 |

## Queries that changed

| Query | | recall@10 | reciprocal rank |
|---|---|---|---|
| C04 green sorcery that finds two basic lands, one onto the battlefield tapped and one into your hand | win | 0.25 → 0.50 | 0.25 → 0.25 |
| C09 a land that taps for any color in your commander's color identity | win | 0.75 → 1.00 | 1.00 → 1.00 |
| C10 an enchantment that doubles the tokens you create | loss | 0.50 → 0.25 | 1.00 → 1.00 |
| C14 whenever an opponent casts a spell, you draw a card unless they pay one | win | 1.00 → 1.00 | 0.20 → 0.33 |
| C15 whenever an opponent draws a card, you get a Treasure unless they pay two | win | 0.00 → 1.00 | 0.00 → 0.10 |
| C16 bounce a nonland permanent you don't control, or overload it to bounce all of them | loss | 1.00 → 0.00 | 0.10 → 0.00 |
| C17 a land that gives you no maximum hand size | win | 1.00 → 1.00 | 0.50 → 1.00 |
| P01 counterspells | loss | 0.80 → 0.80 | 1.00 → 0.50 |
| P07 get cards back from my graveyard to my hand | win | 0.22 → 0.33 | 0.33 → 0.50 |
| P08 make Treasure tokens | win | 1.00 → 1.00 | 0.50 → 1.00 |
| P12 tutor: search my library for a creature card | loss | 1.00 → 0.00 | 0.50 → 0.00 |
| P13 lands that tap for two colors | loss | 1.00 → 1.00 | 0.33 → 0.25 |
| P16 stop opposing creatures from blocking | loss | 0.17 → 0.17 | 0.17 → 0.14 |
| P19 flicker a creature: exile it and return it to the battlefield | loss | 1.00 → 1.00 | 0.25 → 0.20 |
| P21 copy my instant and sorcery spells | win | 1.00 → 1.00 | 0.17 → 0.50 |
| R01 How many cards must a Commander deck have? | loss | 1.00 → 1.00 | 0.33 → 0.25 |
| R12 A planeswalker says it can be your commander. Is that allowed? | loss | 1.00 → 1.00 | 0.33 → 0.14 |
| R19 How does lifelink work? | loss | 1.00 → 1.00 | 0.25 → 0.20 |
| R24 How does cascade work? | win | 1.00 → 1.00 | 0.50 → 1.00 |
| R31 How does double strike deal damage in combat? | loss | 1.00 → 1.00 | 0.50 → 0.33 |
| R34 What happens if I have to draw from an empty library? | loss | 1.00 → 1.00 | 1.00 → 0.50 |
| R36 Why can't my creature use a tap ability the turn it enters? | win | 1.00 → 1.00 | 0.17 → 0.25 |
| R39 What is the mana value of a card with X in its cost? | loss | 1.00 → 1.00 | 1.00 → 0.33 |
| R40 How many lands can I play each turn? | loss | 1.00 → 1.00 | 0.50 → 0.33 |
