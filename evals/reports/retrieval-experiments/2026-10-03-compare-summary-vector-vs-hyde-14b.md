# Retrieval comparison: `summary-vector` → `hyde-14b`, 2026-10-03

Dev set (44 card queries, 45 rules questions). Baseline: Pilot: the summary embedded as its own vector and fused with the card vector and the keyword arm (weights 1, 1, 0.5); catalogue queries unchanged from embed-8b. Candidate: The best so far, fused with a search for the card or rule text qwen3:14b writes for the query (HyDE). Script: `scripts/retrieval_experiments.py`.

Deltas are in points (candidate minus baseline). The 95% intervals are bootstrap intervals over queries: with this few queries they're wide, and an interval that includes 0 means the change could be noise.

| Set | queries | recall@10 | MRR@10 | wins / losses / ties |
|---|---|---|---|---|
| Cards | 44 | 0.78 → 0.69 (-8.3, 95% [-19.6, +3.0]) | 0.64 → 0.62 (-2.0, 95% [-13.0, +8.8]) | 13 / 16 / 15 |
| Rules | 45 | 1.00 → 1.00 (+0.0, 95% [+0.0, +0.0]) | 0.76 → 0.76 (+0.3, 95% [-3.0, +3.6]) | 4 / 5 / 36 |

## Adoption rule

**Cards: do not adopt.** Candidate latency: median 4518 ms, p90 7359 ms.

- FAIL: best gain -2.0 points (needs +3)
- FAIL: 13 wins, 16 losses (needs more wins)
- FAIL: worst change -8.3 points (may drop at most 2)
- FAIL: median search 4518 ms (budget 2 s)

**Rules: do not adopt.** Candidate latency: median 8993 ms, p90 12156 ms.

- FAIL: best gain +0.3 points (needs +3)
- FAIL: 4 wins, 5 losses (needs more wins)
- pass: worst change +0.0 points (may drop at most 2)
- FAIL: median search 8993 ms (budget 2 s)

## By group (recall@10, MRR@10)

| Group | recall@10 | MRR@10 |
|---|---|---|
| cards, catalogue (23) | 0.77 → 0.68 | 0.57 → 0.58 |
| cards, `paraphrase` (18) | 0.71 → 0.65 | 0.48 → 0.59 |
| cards, `term` (3) | 1.00 → 0.64 | 0.83 → 0.53 |
| cards, `name` (2) | 1.00 → 1.00 | 1.00 → 0.55 |
| cards, pool (21) | 0.78 → 0.70 | 0.73 → 0.68 |
| cards, `need` (21) | 0.78 → 0.70 | 0.73 → 0.68 |
| rules, `commander` (14) | 1.00 → 1.00 | 0.67 → 0.71 |
| rules, `keyword` (16) | 1.00 → 1.00 | 0.88 → 0.88 |
| rules, `general` (15) | 1.00 → 1.00 | 0.72 → 0.69 |

## Queries that changed

| Query | | recall@10 | reciprocal rank |
|---|---|---|---|
| C01 an artifact that costs one and taps for two colorless mana | loss | 1.00 → 0.00 | 0.50 → 0.00 |
| C04 green sorcery that finds two basic lands, one onto the battlefield tapped and one into your hand | win | 0.25 → 0.25 | 0.50 → 1.00 |
| C05 four-mana sorcery that destroys all creatures, and they can't be regenerated | win | 1.00 → 1.00 | 0.50 → 1.00 |
| C06 counter target spell for two blue mana | win | 0.00 → 1.00 | 0.00 → 0.12 |
| C07 black sorcery that searches your library for any card and puts it into your hand | win | 0.00 → 1.00 | 0.00 → 1.00 |
| C08 equipment that draws two cards when the equipped creature dies | loss | 1.00 → 0.00 | 1.00 → 0.00 |
| C09 a land that taps for any color in your commander's color identity | loss | 1.00 → 0.50 | 1.00 → 1.00 |
| C11 sacrifice a creature to add colorless mana | win | 0.00 → 0.50 | 0.00 → 0.11 |
| C13 equipment that gives a creature haste and stops it being targeted | loss | 1.00 → 0.00 | 0.33 → 0.00 |
| C14 whenever an opponent casts a spell, you draw a card unless they pay one | win | 1.00 → 1.00 | 0.33 → 1.00 |
| C15 whenever an opponent draws a card, you get a Treasure unless they pay two | win | 1.00 → 1.00 | 0.17 → 0.33 |
| C16 bounce a nonland permanent you don't control, or overload it to bounce all of them | win | 1.00 → 1.00 | 0.25 → 0.50 |
| C17 a land that gives you no maximum hand size | win | 1.00 → 1.00 | 0.50 → 1.00 |
| C19 ripple | loss | 1.00 → 0.80 | 1.00 → 1.00 |
| C20 epic | loss | 1.00 → 0.80 | 1.00 → 0.50 |
| C21 gravestorm | loss | 1.00 → 0.33 | 0.50 → 0.10 |
| C23 kodamas reach | loss | 1.00 → 1.00 | 1.00 → 0.10 |
| P03 cheap ramp | win | 0.33 → 0.33 | 0.20 → 0.33 |
| P05 board wipes that kill every creature | win | 0.71 → 0.86 | 1.00 → 1.00 |
| P07 get cards back from my graveyard to my hand | loss | 0.33 → 0.11 | 0.25 → 0.14 |
| P10 cheap burn to kill a creature | loss | 1.00 → 0.75 | 0.50 → 0.25 |
| P12 tutor: search my library for a creature card | loss | 1.00 → 1.00 | 1.00 → 0.50 |
| P13 lands that tap for two colors | win | 1.00 → 1.00 | 0.33 → 0.50 |
| P16 stop opposing creatures from blocking | loss | 1.00 → 0.83 | 1.00 → 1.00 |
| P17 mill an opponent | loss | 1.00 → 0.75 | 1.00 → 0.50 |
| P18 tap down opposing creatures | loss | 0.67 → 0.33 | 0.50 → 0.25 |
| P19 flicker a creature: exile it and return it to the battlefield | win | 1.00 → 1.00 | 0.50 → 1.00 |
| P20 take an extra turn | loss | 1.00 → 0.50 | 1.00 → 1.00 |
| P21 copy my instant and sorcery spells | loss | 1.00 → 1.00 | 0.50 → 0.25 |
| R02 Can I run more than one copy of a card in Commander? | win | 1.00 → 1.00 | 0.11 → 0.20 |
| R06 What life total do players start with in Commander? | loss | 1.00 → 1.00 | 0.25 → 0.20 |
| R10 How much commander damage makes a player lose? | win | 1.00 → 1.00 | 0.50 → 1.00 |
| R15 How do partner commanders work? | loss | 1.00 → 1.00 | 0.33 → 0.25 |
| R22 When can I cast a spell with flash? | win | 1.00 → 1.00 | 0.25 → 0.33 |
| R35 When does a player lose the game for having no life? | win | 1.00 → 1.00 | 0.33 → 0.50 |
| R37 What happens to a token that leaves the battlefield? | loss | 1.00 → 1.00 | 1.00 → 0.50 |
| R40 How many lands can I play each turn? | loss | 1.00 → 1.00 | 0.25 → 0.20 |
| R42 Which spell on the stack resolves first? | loss | 1.00 → 1.00 | 0.17 → 0.12 |
