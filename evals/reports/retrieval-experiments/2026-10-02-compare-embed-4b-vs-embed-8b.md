# Retrieval comparison: `embed-4b` → `embed-8b`, 2026-10-02

Dev set (44 card queries, 45 rules questions). Baseline: qwen3-embedding:4b truncated to 1,024 dimensions; cards hybrid with the keyword arm at 0.5 (the current best), rules vector. Candidate: qwen3-embedding:8b truncated to 1,024 dimensions; cards hybrid with the keyword arm at 0.5 (the current best), rules vector. Script: `scripts/retrieval_experiments.py`.

Deltas are in points (candidate minus baseline). The 95% intervals are bootstrap intervals over queries: with this few queries they're wide, and an interval that includes 0 means the change could be noise.

| Set | queries | recall@10 | MRR@10 | wins / losses / ties |
|---|---|---|---|---|
| Cards | 44 | 0.63 → 0.72 (+9.4, 95% [+1.7, +18.3]) | 0.58 → 0.63 (+4.9, 95% [-5.0, +14.3]) | 20 / 7 / 17 |
| Rules | 45 | 0.98 → 1.00 (+2.2, 95% [+0.0, +6.7]) | 0.76 → 0.76 (+0.0, 95% [-9.2, +9.2]) | 10 / 10 / 25 |

## Adoption rule

**Cards: adopt.** Candidate latency: median 207 ms, p90 487 ms.

- pass: best gain +9.4 points (needs +3)
- pass: 20 wins, 7 losses (needs more wins)
- pass: worst change +4.9 points (may drop at most 2)
- pass: median search 207 ms (budget 2 s)

**Rules: do not adopt.** Candidate latency: median 56 ms, p90 67 ms.

- FAIL: best gain +2.2 points (needs +3)
- FAIL: 10 wins, 10 losses (needs more wins)
- pass: worst change +0.0 points (may drop at most 2)
- pass: median search 56 ms (budget 2 s)

## By group (recall@10, MRR@10)

| Group | recall@10 | MRR@10 |
|---|---|---|
| cards, catalogue (23) | 0.63 → 0.77 | 0.53 → 0.57 |
| cards, `paraphrase` (18) | 0.54 → 0.71 | 0.39 → 0.48 |
| cards, `term` (3) | 0.93 → 1.00 | 1.00 → 0.83 |
| cards, `name` (2) | 1.00 → 1.00 | 1.00 → 1.00 |
| cards, pool (21) | 0.62 → 0.67 | 0.65 → 0.70 |
| cards, `need` (21) | 0.62 → 0.67 | 0.65 → 0.70 |
| rules, `commander` (14) | 0.93 → 1.00 | 0.73 → 0.67 |
| rules, `keyword` (16) | 1.00 → 1.00 | 0.77 → 0.88 |
| rules, `general` (15) | 1.00 → 1.00 | 0.78 → 0.72 |

## Queries that changed

| Query | | recall@10 | reciprocal rank |
|---|---|---|---|
| C01 an artifact that costs one and taps for two colorless mana | win | 0.00 → 1.00 | 0.00 → 0.50 |
| C02 one-mana white instant that exiles a creature, and its controller gains life equal to its power | win | 0.50 → 0.50 | 0.20 → 0.50 |
| C04 green sorcery that finds two basic lands, one onto the battlefield tapped and one into your hand | win | 0.25 → 0.25 | 0.17 → 0.50 |
| C05 four-mana sorcery that destroys all creatures, and they can't be regenerated | win | 1.00 → 1.00 | 0.33 → 0.50 |
| C07 black sorcery that searches your library for any card and puts it into your hand | loss | 0.50 → 0.00 | 0.10 → 0.00 |
| C10 an enchantment that doubles the tokens you create | win | 0.50 → 1.00 | 1.00 → 1.00 |
| C13 equipment that gives a creature haste and stops it being targeted | win | 0.00 → 1.00 | 0.00 → 0.33 |
| C14 whenever an opponent casts a spell, you draw a card unless they pay one | win | 0.00 → 1.00 | 0.00 → 0.33 |
| C16 bounce a nonland permanent you don't control, or overload it to bounce all of them | win | 1.00 → 1.00 | 0.12 → 0.25 |
| C17 a land that gives you no maximum hand size | loss | 1.00 → 1.00 | 1.00 → 0.50 |
| C20 epic | win | 0.80 → 1.00 | 1.00 → 1.00 |
| C21 gravestorm | loss | 1.00 → 1.00 | 1.00 → 0.50 |
| P02 ways to destroy an artifact or enchantment | win | 0.25 → 0.25 | 0.50 → 1.00 |
| P03 cheap ramp | win | 0.17 → 0.33 | 0.17 → 0.33 |
| P04 repeatable card draw | loss | 0.33 → 0.33 | 1.00 → 0.20 |
| P05 board wipes that kill every creature | win | 0.43 → 0.43 | 0.12 → 1.00 |
| P06 return a creature card from my graveyard to the battlefield | win | 0.75 → 0.75 | 0.50 → 1.00 |
| P07 get cards back from my graveyard to my hand | win | 0.22 → 0.33 | 1.00 → 1.00 |
| P08 make Treasure tokens | win | 0.67 → 1.00 | 1.00 → 1.00 |
| P09 give my creature hexproof, shroud or ward | loss | 0.75 → 0.50 | 0.33 → 0.50 |
| P10 cheap burn to kill a creature | win | 0.50 → 0.75 | 0.33 → 0.33 |
| P12 tutor: search my library for a creature card | win | 1.00 → 1.00 | 0.50 → 1.00 |
| P13 lands that tap for two colors | win | 1.00 → 1.00 | 0.50 → 1.00 |
| P16 stop opposing creatures from blocking | win | 0.33 → 0.67 | 0.50 → 0.33 |
| P18 tap down opposing creatures | loss | 0.67 → 0.67 | 1.00 → 0.33 |
| P19 flicker a creature: exile it and return it to the battlefield | loss | 1.00 → 1.00 | 1.00 → 0.25 |
| P21 copy my instant and sorcery spells | win | 1.00 → 1.00 | 0.17 → 0.50 |
| R01 How many cards must a Commander deck have? | win | 1.00 → 1.00 | 0.50 → 1.00 |
| R02 Can I run more than one copy of a card in Commander? | loss | 1.00 → 1.00 | 0.33 → 0.11 |
| R06 What life total do players start with in Commander? | win | 1.00 → 1.00 | 0.14 → 0.25 |
| R08 What happens when my commander would die or be exiled? | win | 1.00 → 1.00 | 0.25 → 0.50 |
| R09 Can my commander go to the command zone instead of my hand or library? | win | 0.00 → 1.00 | 0.00 → 0.50 |
| R10 How much commander damage makes a player lose? | loss | 1.00 → 1.00 | 1.00 → 0.50 |
| R12 A planeswalker says it can be your commander. Is that allowed? | loss | 1.00 → 1.00 | 1.00 → 0.33 |
| R13 Where does my commander start the game? | loss | 1.00 → 1.00 | 1.00 → 0.20 |
| R15 How do partner commanders work? | loss | 1.00 → 1.00 | 0.50 → 0.33 |
| R16 Is the first mulligan free in a multiplayer game? | win | 1.00 → 1.00 | 0.33 → 0.50 |
| R17 What does deathtouch do? | win | 1.00 → 1.00 | 0.50 → 1.00 |
| R21 Can an indestructible creature be destroyed by damage? | win | 1.00 → 1.00 | 0.50 → 1.00 |
| R22 When can I cast a spell with flash? | loss | 1.00 → 1.00 | 0.50 → 0.25 |
| R28 What happens to a permanent when it phases out? | win | 1.00 → 1.00 | 0.33 → 1.00 |
| R31 How does double strike deal damage in combat? | win | 1.00 → 1.00 | 0.50 → 1.00 |
| R34 What happens if I have to draw from an empty library? | win | 1.00 → 1.00 | 0.50 → 1.00 |
| R35 When does a player lose the game for having no life? | loss | 1.00 → 1.00 | 0.50 → 0.33 |
| R40 How many lands can I play each turn? | loss | 1.00 → 1.00 | 0.33 → 0.25 |
| R41 What is the maximum hand size, and when do I discard down to it? | loss | 1.00 → 1.00 | 1.00 → 0.50 |
| R42 Which spell on the stack resolves first? | loss | 1.00 → 1.00 | 1.00 → 0.17 |
