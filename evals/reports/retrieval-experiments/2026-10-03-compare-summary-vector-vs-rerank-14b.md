# Retrieval comparison: `summary-vector` → `rerank-14b`, 2026-10-03

Dev set (44 card queries, 45 rules questions). Baseline: Pilot: the summary embedded as its own vector and fused with the card vector and the keyword arm (weights 1, 1, 0.5); catalogue queries unchanged from embed-8b. Candidate: The best so far (summary-vector), with qwen3:14b reranking the top 20 cards or rules into the final 10. Script: `scripts/retrieval_experiments.py`.

Deltas are in points (candidate minus baseline). The 95% intervals are bootstrap intervals over queries: with this few queries they're wide, and an interval that includes 0 means the change could be noise.

| Set | queries | recall@10 | MRR@10 | wins / losses / ties |
|---|---|---|---|---|
| Cards | 44 | 0.78 → 0.80 (+2.5, 95% [-2.1, +7.2]) | 0.64 → 0.87 (+22.1, 95% [+12.6, +32.2]) | 17 / 3 / 24 |
| Rules | 45 | 1.00 → 0.99 (-1.1, 95% [-3.3, +0.0]) | 0.76 → 0.87 (+11.0, 95% [+3.8, +19.0]) | 12 / 3 / 30 |

## Adoption rule

**Cards: do not adopt.** Candidate latency: median 9250 ms, p90 10284 ms.

- pass: best gain +22.1 points (needs +3)
- pass: 17 wins, 3 losses (needs more wins)
- pass: worst change +2.5 points (may drop at most 2)
- FAIL: median search 9250 ms (budget 2 s)

**Rules: do not adopt.** Candidate latency: median 10260 ms, p90 11797 ms.

- pass: best gain +11.0 points (needs +3)
- pass: 12 wins, 3 losses (needs more wins)
- pass: worst change -1.1 points (may drop at most 2)
- FAIL: median search 10260 ms (budget 2 s)

## By group (recall@10, MRR@10)

| Group | recall@10 | MRR@10 |
|---|---|---|
| cards, catalogue (23) | 0.77 → 0.80 | 0.57 → 0.87 |
| cards, `paraphrase` (18) | 0.71 → 0.75 | 0.48 → 0.86 |
| cards, `term` (3) | 1.00 → 1.00 | 0.83 → 0.83 |
| cards, `name` (2) | 1.00 → 1.00 | 1.00 → 1.00 |
| cards, pool (21) | 0.78 → 0.80 | 0.73 → 0.86 |
| cards, `need` (21) | 0.78 → 0.80 | 0.73 → 0.86 |
| rules, `commander` (14) | 1.00 → 1.00 | 0.67 → 0.83 |
| rules, `keyword` (16) | 1.00 → 0.97 | 0.88 → 0.91 |
| rules, `general` (15) | 1.00 → 1.00 | 0.72 → 0.87 |

## Queries that changed

| Query | | recall@10 | reciprocal rank |
|---|---|---|---|
| C01 an artifact that costs one and taps for two colorless mana | win | 1.00 → 1.00 | 0.50 → 1.00 |
| C02 one-mana white instant that exiles a creature, and its controller gains life equal to its power | win | 0.50 → 0.50 | 0.50 → 1.00 |
| C04 green sorcery that finds two basic lands, one onto the battlefield tapped and one into your hand | win | 0.25 → 0.50 | 0.50 → 1.00 |
| C05 four-mana sorcery that destroys all creatures, and they can't be regenerated | win | 1.00 → 1.00 | 0.50 → 1.00 |
| C07 black sorcery that searches your library for any card and puts it into your hand | win | 0.00 → 0.50 | 0.00 → 1.00 |
| C11 sacrifice a creature to add colorless mana | win | 0.00 → 0.50 | 0.00 → 0.50 |
| C13 equipment that gives a creature haste and stops it being targeted | loss | 1.00 → 0.50 | 0.33 → 1.00 |
| C14 whenever an opponent casts a spell, you draw a card unless they pay one | win | 1.00 → 1.00 | 0.33 → 1.00 |
| C15 whenever an opponent draws a card, you get a Treasure unless they pay two | win | 1.00 → 1.00 | 0.17 → 1.00 |
| C16 bounce a nonland permanent you don't control, or overload it to bounce all of them | win | 1.00 → 1.00 | 0.25 → 1.00 |
| C17 a land that gives you no maximum hand size | win | 1.00 → 1.00 | 0.50 → 1.00 |
| P04 repeatable card draw | win | 0.33 → 0.67 | 0.50 → 0.25 |
| P06 return a creature card from my graveyard to the battlefield | loss | 0.75 → 0.75 | 1.00 → 0.50 |
| P07 get cards back from my graveyard to my hand | win | 0.33 → 0.33 | 0.25 → 1.00 |
| P10 cheap burn to kill a creature | win | 1.00 → 1.00 | 0.50 → 1.00 |
| P11 steal an opponent's creature | win | 0.00 → 0.33 | 0.00 → 0.14 |
| P13 lands that tap for two colors | win | 1.00 → 1.00 | 0.33 → 1.00 |
| P18 tap down opposing creatures | loss | 0.67 → 0.33 | 0.50 → 1.00 |
| P19 flicker a creature: exile it and return it to the battlefield | win | 1.00 → 1.00 | 0.50 → 1.00 |
| P21 copy my instant and sorcery spells | win | 1.00 → 1.00 | 0.50 → 1.00 |
| R02 Can I run more than one copy of a card in Commander? | win | 1.00 → 1.00 | 0.11 → 0.50 |
| R06 What life total do players start with in Commander? | win | 1.00 → 1.00 | 0.25 → 0.33 |
| R08 What happens when my commander would die or be exiled? | loss | 1.00 → 1.00 | 0.50 → 0.33 |
| R09 Can my commander go to the command zone instead of my hand or library? | win | 1.00 → 1.00 | 0.50 → 1.00 |
| R12 A planeswalker says it can be your commander. Is that allowed? | win | 1.00 → 1.00 | 0.33 → 1.00 |
| R13 Where does my commander start the game? | win | 1.00 → 1.00 | 0.20 → 1.00 |
| R15 How do partner commanders work? | win | 1.00 → 1.00 | 0.33 → 0.50 |
| R17 What does deathtouch do? | loss | 1.00 → 1.00 | 1.00 → 0.50 |
| R22 When can I cast a spell with flash? | win | 1.00 → 1.00 | 0.25 → 1.00 |
| R28 What happens to a permanent when it phases out? | loss | 1.00 → 0.50 | 1.00 → 1.00 |
| R35 When does a player lose the game for having no life? | win | 1.00 → 1.00 | 0.33 → 0.50 |
| R36 Why can't my creature use a tap ability the turn it enters? | win | 1.00 → 1.00 | 0.50 → 1.00 |
| R40 How many lands can I play each turn? | win | 1.00 → 1.00 | 0.25 → 1.00 |
| R42 Which spell on the stack resolves first? | win | 1.00 → 1.00 | 0.17 → 0.50 |
| R43 How do extra turns work? | win | 1.00 → 1.00 | 0.50 → 1.00 |
