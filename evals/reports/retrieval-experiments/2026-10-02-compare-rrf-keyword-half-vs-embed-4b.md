# Retrieval comparison: `rrf-keyword-half` → `embed-4b`, 2026-10-02

Dev set (44 card queries, 45 rules questions). Baseline: Hybrid with the keyword arm's fusion weight halved (0.5). Candidate: qwen3-embedding:4b truncated to 1,024 dimensions; cards hybrid with the keyword arm at 0.5 (the current best), rules vector. Script: `scripts/retrieval_experiments.py`.

Deltas are in points (candidate minus baseline). The 95% intervals are bootstrap intervals over queries: with this few queries they're wide, and an interval that includes 0 means the change could be noise.

| Set | queries | recall@10 | MRR@10 | wins / losses / ties |
|---|---|---|---|---|
| Cards | 44 | 0.62 → 0.63 (+1.1, 95% [-6.0, +7.7]) | 0.50 → 0.58 (+8.5, 95% [+0.5, +16.6]) | 18 / 9 / 17 |
| Rules | 45 | 0.97 → 0.98 (+1.1, 95% [-5.6, +7.8]) | 0.70 → 0.76 (+5.8, 95% [-3.3, +14.7]) | 17 / 7 / 21 |

## Adoption rule

**Cards: adopt.** Candidate latency: median 201 ms, p90 447 ms.

- pass: best gain +8.5 points (needs +3)
- pass: 18 wins, 9 losses (needs more wins)
- pass: worst change +1.1 points (may drop at most 2)
- pass: median search 201 ms (budget 2 s)

**Rules: adopt.** Candidate latency: median 42 ms, p90 55 ms.

- pass: best gain +5.8 points (needs +3)
- pass: 17 wins, 7 losses (needs more wins)
- pass: worst change +1.1 points (may drop at most 2)
- pass: median search 42 ms (budget 2 s)

## By group (recall@10, MRR@10)

| Group | recall@10 | MRR@10 |
|---|---|---|
| cards, catalogue (23) | 0.64 → 0.63 | 0.48 → 0.53 |
| cards, `paraphrase` (18) | 0.54 → 0.54 | 0.37 → 0.39 |
| cards, `term` (3) | 1.00 → 0.93 | 0.83 → 1.00 |
| cards, `name` (2) | 1.00 → 1.00 | 1.00 → 1.00 |
| cards, pool (21) | 0.59 → 0.62 | 0.52 → 0.65 |
| cards, `need` (21) | 0.59 → 0.62 | 0.52 → 0.65 |
| rules, `commander` (14) | 0.93 → 0.93 | 0.59 → 0.73 |
| rules, `keyword` (16) | 0.97 → 1.00 | 0.70 → 0.77 |
| rules, `general` (15) | 1.00 → 1.00 | 0.81 → 0.78 |

## Queries that changed

| Query | | recall@10 | reciprocal rank |
|---|---|---|---|
| C02 one-mana white instant that exiles a creature, and its controller gains life equal to its power | loss | 0.50 → 0.50 | 0.25 → 0.20 |
| C04 green sorcery that finds two basic lands, one onto the battlefield tapped and one into your hand | loss | 0.25 → 0.25 | 0.33 → 0.17 |
| C05 four-mana sorcery that destroys all creatures, and they can't be regenerated | win | 0.50 → 1.00 | 0.33 → 0.33 |
| C07 black sorcery that searches your library for any card and puts it into your hand | win | 0.00 → 0.50 | 0.00 → 0.10 |
| C08 equipment that draws two cards when the equipped creature dies | win | 1.00 → 1.00 | 0.20 → 1.00 |
| C09 a land that taps for any color in your commander's color identity | win | 0.75 → 1.00 | 1.00 → 1.00 |
| C10 an enchantment that doubles the tokens you create | loss | 0.75 → 0.50 | 1.00 → 1.00 |
| C14 whenever an opponent casts a spell, you draw a card unless they pay one | loss | 1.00 → 0.00 | 0.20 → 0.00 |
| C15 whenever an opponent draws a card, you get a Treasure unless they pay two | win | 1.00 → 1.00 | 0.11 → 0.17 |
| C16 bounce a nonland permanent you don't control, or overload it to bounce all of them | loss | 1.00 → 1.00 | 0.14 → 0.12 |
| C19 ripple | win | 1.00 → 1.00 | 0.50 → 1.00 |
| C20 epic | loss | 1.00 → 0.80 | 1.00 → 1.00 |
| P02 ways to destroy an artifact or enchantment | win | 0.25 → 0.25 | 0.25 → 0.50 |
| P03 cheap ramp | win | 0.00 → 0.17 | 0.00 → 0.17 |
| P04 repeatable card draw | win | 0.33 → 0.33 | 0.33 → 1.00 |
| P05 board wipes that kill every creature | win | 0.14 → 0.43 | 1.00 → 0.12 |
| P06 return a creature card from my graveyard to the battlefield | win | 0.25 → 0.75 | 0.25 → 0.50 |
| P07 get cards back from my graveyard to my hand | win | 0.22 → 0.22 | 0.50 → 1.00 |
| P08 make Treasure tokens | loss | 1.00 → 0.67 | 1.00 → 1.00 |
| P09 give my creature hexproof, shroud or ward | loss | 0.75 → 0.75 | 0.50 → 0.33 |
| P10 cheap burn to kill a creature | win | 0.50 → 0.50 | 0.20 → 0.33 |
| P13 lands that tap for two colors | win | 1.00 → 1.00 | 0.25 → 0.50 |
| P16 stop opposing creatures from blocking | win | 0.17 → 0.33 | 0.14 → 0.50 |
| P17 mill an opponent | win | 0.50 → 0.75 | 1.00 → 1.00 |
| P18 tap down opposing creatures | loss | 1.00 → 0.67 | 0.50 → 1.00 |
| P19 flicker a creature: exile it and return it to the battlefield | win | 1.00 → 1.00 | 0.33 → 1.00 |
| P21 copy my instant and sorcery spells | win | 1.00 → 1.00 | 0.14 → 0.17 |
| R01 How many cards must a Commander deck have? | win | 1.00 → 1.00 | 0.33 → 0.50 |
| R02 Can I run more than one copy of a card in Commander? | win | 0.00 → 1.00 | 0.00 → 0.33 |
| R06 What life total do players start with in Commander? | win | 1.00 → 1.00 | 0.10 → 0.14 |
| R08 What happens when my commander would die or be exiled? | win | 1.00 → 1.00 | 0.20 → 0.25 |
| R09 Can my commander go to the command zone instead of my hand or library? | loss | 1.00 → 0.00 | 0.33 → 0.00 |
| R11 Can a Vehicle be my commander? | win | 1.00 → 1.00 | 0.50 → 1.00 |
| R12 A planeswalker says it can be your commander. Is that allowed? | win | 1.00 → 1.00 | 0.33 → 1.00 |
| R14 Can I include a land with a basic land type that makes a color outside my commander's identity? | win | 1.00 → 1.00 | 0.50 → 1.00 |
| R16 Is the first mulligan free in a multiplayer game? | loss | 1.00 → 1.00 | 0.50 → 0.33 |
| R17 What does deathtouch do? | win | 1.00 → 1.00 | 0.25 → 0.50 |
| R18 How does a creature with trample assign its combat damage? | win | 1.00 → 1.00 | 0.33 → 1.00 |
| R19 How does lifelink work? | win | 1.00 → 1.00 | 0.25 → 0.50 |
| R20 What does hexproof mean? | win | 1.00 → 1.00 | 0.50 → 1.00 |
| R21 Can an indestructible creature be destroyed by damage? | loss | 1.00 → 1.00 | 1.00 → 0.50 |
| R22 When can I cast a spell with flash? | win | 1.00 → 1.00 | 0.33 → 0.50 |
| R24 How does cascade work? | win | 1.00 → 1.00 | 0.50 → 1.00 |
| R28 What happens to a permanent when it phases out? | win | 0.50 → 1.00 | 1.00 → 0.33 |
| R33 What happens to a creature with 0 toughness? | win | 1.00 → 1.00 | 0.50 → 1.00 |
| R34 What happens if I have to draw from an empty library? | loss | 1.00 → 1.00 | 1.00 → 0.50 |
| R35 When does a player lose the game for having no life? | loss | 1.00 → 1.00 | 1.00 → 0.50 |
| R36 Why can't my creature use a tap ability the turn it enters? | win | 1.00 → 1.00 | 0.17 → 0.50 |
| R37 What happens to a token that leaves the battlefield? | win | 1.00 → 1.00 | 0.50 → 1.00 |
| R40 How many lands can I play each turn? | loss | 1.00 → 1.00 | 0.50 → 0.33 |
| R43 How do extra turns work? | loss | 1.00 → 1.00 | 1.00 → 0.50 |
