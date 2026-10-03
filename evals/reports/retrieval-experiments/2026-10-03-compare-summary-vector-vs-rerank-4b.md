# Retrieval comparison: `summary-vector` → `rerank-4b`, 2026-10-03

Dev set (44 card queries, 45 rules questions). Baseline: Pilot: the summary embedded as its own vector and fused with the card vector and the keyword arm (weights 1, 1, 0.5); catalogue queries unchanged from embed-8b. Candidate: As rerank-14b, with qwen3:4b reranking. Script: `scripts/retrieval_experiments.py`.

Deltas are in points (candidate minus baseline). The 95% intervals are bootstrap intervals over queries: with this few queries they're wide, and an interval that includes 0 means the change could be noise.

| Set | queries | recall@10 | MRR@10 | wins / losses / ties |
|---|---|---|---|---|
| Cards | 44 | 0.78 → 0.72 (-5.7, 95% [-11.0, -0.8]) | 0.64 → 0.79 (+14.3, 95% [+4.9, +24.7]) | 8 / 9 / 27 |
| Rules | 45 | 1.00 → 1.00 (+0.0, 95% [+0.0, +0.0]) | 0.76 → 0.87 (+11.2, 95% [+3.3, +20.0]) | 11 / 2 / 32 |

## Adoption rule

**Cards: do not adopt.** Candidate latency: median 877 ms, p90 1044 ms.

- pass: best gain +14.3 points (needs +3)
- FAIL: 8 wins, 9 losses (needs more wins)
- FAIL: worst change -5.7 points (may drop at most 2)
- pass: median search 877 ms (budget 2 s)

**Rules: adopt.** Candidate latency: median 878 ms, p90 974 ms.

- pass: best gain +11.2 points (needs +3)
- pass: 11 wins, 2 losses (needs more wins)
- pass: worst change +0.0 points (may drop at most 2)
- pass: median search 878 ms (budget 2 s)

## By group (recall@10, MRR@10)

| Group | recall@10 | MRR@10 |
|---|---|---|
| cards, catalogue (23) | 0.77 → 0.79 | 0.57 → 0.75 |
| cards, `paraphrase` (18) | 0.71 → 0.74 | 0.48 → 0.71 |
| cards, `term` (3) | 1.00 → 1.00 | 0.83 → 0.83 |
| cards, `name` (2) | 1.00 → 1.00 | 1.00 → 1.00 |
| cards, pool (21) | 0.78 → 0.64 | 0.73 → 0.83 |
| cards, `need` (21) | 0.78 → 0.64 | 0.73 → 0.83 |
| rules, `commander` (14) | 1.00 → 1.00 | 0.67 → 0.78 |
| rules, `keyword` (16) | 1.00 → 1.00 | 0.88 → 0.93 |
| rules, `general` (15) | 1.00 → 1.00 | 0.72 → 0.90 |

## Queries that changed

| Query | | recall@10 | reciprocal rank |
|---|---|---|---|
| C02 one-mana white instant that exiles a creature, and its controller gains life equal to its power | win | 0.50 → 0.50 | 0.50 → 1.00 |
| C05 four-mana sorcery that destroys all creatures, and they can't be regenerated | win | 1.00 → 1.00 | 0.50 → 1.00 |
| C07 black sorcery that searches your library for any card and puts it into your hand | win | 0.00 → 0.50 | 0.00 → 1.00 |
| C14 whenever an opponent casts a spell, you draw a card unless they pay one | win | 1.00 → 1.00 | 0.33 → 1.00 |
| C15 whenever an opponent draws a card, you get a Treasure unless they pay two | win | 1.00 → 1.00 | 0.17 → 1.00 |
| C16 bounce a nonland permanent you don't control, or overload it to bounce all of them | win | 1.00 → 1.00 | 0.25 → 1.00 |
| P03 cheap ramp | win | 0.33 → 0.33 | 0.20 → 0.50 |
| P06 return a creature card from my graveyard to the battlefield | loss | 0.75 → 0.50 | 1.00 → 0.50 |
| P07 get cards back from my graveyard to my hand | loss | 0.33 → 0.22 | 0.25 → 1.00 |
| P08 make Treasure tokens | loss | 1.00 → 0.33 | 1.00 → 1.00 |
| P10 cheap burn to kill a creature | loss | 1.00 → 0.75 | 0.50 → 1.00 |
| P13 lands that tap for two colors | win | 1.00 → 1.00 | 0.33 → 1.00 |
| P14 mana rocks that tap for any color | loss | 1.00 → 0.67 | 1.00 → 1.00 |
| P16 stop opposing creatures from blocking | loss | 1.00 → 0.67 | 1.00 → 0.33 |
| P17 mill an opponent | loss | 1.00 → 0.75 | 1.00 → 1.00 |
| P18 tap down opposing creatures | loss | 0.67 → 0.33 | 0.50 → 1.00 |
| P19 flicker a creature: exile it and return it to the battlefield | loss | 1.00 → 0.50 | 0.50 → 1.00 |
| R06 What life total do players start with in Commander? | win | 1.00 → 1.00 | 0.25 → 0.33 |
| R09 Can my commander go to the command zone instead of my hand or library? | win | 1.00 → 1.00 | 0.50 → 1.00 |
| R12 A planeswalker says it can be your commander. Is that allowed? | win | 1.00 → 1.00 | 0.33 → 1.00 |
| R13 Where does my commander start the game? | win | 1.00 → 1.00 | 0.20 → 0.50 |
| R19 How does lifelink work? | win | 1.00 → 1.00 | 0.50 → 1.00 |
| R22 When can I cast a spell with flash? | win | 1.00 → 1.00 | 0.25 → 1.00 |
| R25 How does storm copy a spell? | loss | 1.00 → 1.00 | 1.00 → 0.50 |
| R35 When does a player lose the game for having no life? | win | 1.00 → 1.00 | 0.33 → 1.00 |
| R36 Why can't my creature use a tap ability the turn it enters? | win | 1.00 → 1.00 | 0.50 → 1.00 |
| R37 What happens to a token that leaves the battlefield? | loss | 1.00 → 1.00 | 1.00 → 0.50 |
| R40 How many lands can I play each turn? | win | 1.00 → 1.00 | 0.25 → 1.00 |
| R42 Which spell on the stack resolves first? | win | 1.00 → 1.00 | 0.17 → 1.00 |
| R43 How do extra turns work? | win | 1.00 → 1.00 | 0.50 → 1.00 |
