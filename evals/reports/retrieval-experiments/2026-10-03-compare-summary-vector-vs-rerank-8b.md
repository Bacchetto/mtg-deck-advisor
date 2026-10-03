# Retrieval comparison: `summary-vector` → `rerank-8b`, 2026-10-03

Dev set (44 card queries, 45 rules questions). Baseline: Pilot: the summary embedded as its own vector and fused with the card vector and the keyword arm (weights 1, 1, 0.5); catalogue queries unchanged from embed-8b. Candidate: As rerank-14b, with qwen3:8b reranking: it fits beside the 8b embedder in VRAM. Script: `scripts/retrieval_experiments.py`.

Deltas are in points (candidate minus baseline). The 95% intervals are bootstrap intervals over queries: with this few queries they're wide, and an interval that includes 0 means the change could be noise.

| Set | queries | recall@10 | MRR@10 | wins / losses / ties |
|---|---|---|---|---|
| Cards | 44 | 0.78 → 0.81 (+3.7, 95% [-2.5, +10.7]) | 0.64 → 0.83 (+18.7, 95% [+9.5, +28.2]) | 19 / 6 / 19 |
| Rules | 45 | 1.00 → 0.97 (-3.3, 95% [-8.9, +0.0]) | 0.76 → 0.84 (+7.5, 95% [+1.5, +14.6]) | 9 / 3 / 33 |

## Adoption rule

**Cards: adopt.** Candidate latency: median 1304 ms, p90 1626 ms.

- pass: best gain +18.7 points (needs +3)
- pass: 19 wins, 6 losses (needs more wins)
- pass: worst change +3.7 points (may drop at most 2)
- pass: median search 1304 ms (budget 2 s)

**Rules: do not adopt.** Candidate latency: median 1544 ms, p90 1745 ms.

- pass: best gain +7.5 points (needs +3)
- pass: 9 wins, 3 losses (needs more wins)
- FAIL: worst change -3.3 points (may drop at most 2)
- pass: median search 1544 ms (budget 2 s)

## By group (recall@10, MRR@10)

| Group | recall@10 | MRR@10 |
|---|---|---|
| cards, catalogue (23) | 0.77 → 0.86 | 0.57 → 0.82 |
| cards, `paraphrase` (18) | 0.71 → 0.82 | 0.48 → 0.80 |
| cards, `term` (3) | 1.00 → 1.00 | 0.83 → 0.83 |
| cards, `name` (2) | 1.00 → 1.00 | 1.00 → 1.00 |
| cards, pool (21) | 0.78 → 0.76 | 0.73 → 0.84 |
| cards, `need` (21) | 0.78 → 0.76 | 0.73 → 0.84 |
| rules, `commander` (14) | 1.00 → 0.89 | 0.67 → 0.77 |
| rules, `keyword` (16) | 1.00 → 1.00 | 0.88 → 0.93 |
| rules, `general` (15) | 1.00 → 1.00 | 0.72 → 0.80 |

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
| C13 equipment that gives a creature haste and stops it being targeted | win | 1.00 → 1.00 | 0.33 → 1.00 |
| C14 whenever an opponent casts a spell, you draw a card unless they pay one | win | 1.00 → 1.00 | 0.33 → 1.00 |
| C15 whenever an opponent draws a card, you get a Treasure unless they pay two | win | 1.00 → 1.00 | 0.17 → 1.00 |
| C16 bounce a nonland permanent you don't control, or overload it to bounce all of them | win | 1.00 → 1.00 | 0.25 → 1.00 |
| C17 a land that gives you no maximum hand size | win | 1.00 → 1.00 | 0.50 → 1.00 |
| C19 ripple | loss | 1.00 → 1.00 | 1.00 → 0.50 |
| C21 gravestorm | win | 1.00 → 1.00 | 0.50 → 1.00 |
| P03 cheap ramp | win | 0.33 → 0.33 | 0.20 → 0.25 |
| P04 repeatable card draw | win | 0.33 → 0.67 | 0.50 → 1.00 |
| P05 board wipes that kill every creature | win | 0.71 → 0.86 | 1.00 → 1.00 |
| P06 return a creature card from my graveyard to the battlefield | loss | 0.75 → 0.50 | 1.00 → 0.50 |
| P07 get cards back from my graveyard to my hand | win | 0.33 → 0.33 | 0.25 → 1.00 |
| P10 cheap burn to kill a creature | win | 1.00 → 1.00 | 0.50 → 1.00 |
| P11 steal an opponent's creature | win | 0.00 → 0.33 | 0.00 → 0.10 |
| P13 lands that tap for two colors | loss | 1.00 → 0.75 | 0.33 → 0.33 |
| P16 stop opposing creatures from blocking | loss | 1.00 → 0.83 | 1.00 → 1.00 |
| P18 tap down opposing creatures | win | 0.67 → 0.67 | 0.50 → 1.00 |
| P19 flicker a creature: exile it and return it to the battlefield | loss | 1.00 → 0.50 | 0.50 → 1.00 |
| R02 Can I run more than one copy of a card in Commander? | loss | 1.00 → 0.00 | 0.11 → 0.00 |
| R08 What happens when my commander would die or be exiled? | win | 1.00 → 1.00 | 0.50 → 1.00 |
| R09 Can my commander go to the command zone instead of my hand or library? | win | 1.00 → 1.00 | 0.50 → 1.00 |
| R10 How much commander damage makes a player lose? | loss | 1.00 → 0.50 | 0.50 → 0.50 |
| R12 A planeswalker says it can be your commander. Is that allowed? | win | 1.00 → 1.00 | 0.33 → 0.50 |
| R13 Where does my commander start the game? | win | 1.00 → 1.00 | 0.20 → 0.50 |
| R22 When can I cast a spell with flash? | win | 1.00 → 1.00 | 0.25 → 1.00 |
| R35 When does a player lose the game for having no life? | win | 1.00 → 1.00 | 0.33 → 0.50 |
| R37 What happens to a token that leaves the battlefield? | loss | 1.00 → 1.00 | 1.00 → 0.50 |
| R40 How many lands can I play each turn? | win | 1.00 → 1.00 | 0.25 → 1.00 |
| R42 Which spell on the stack resolves first? | win | 1.00 → 1.00 | 0.17 → 0.50 |
| R43 How do extra turns work? | win | 1.00 → 1.00 | 0.50 → 1.00 |
