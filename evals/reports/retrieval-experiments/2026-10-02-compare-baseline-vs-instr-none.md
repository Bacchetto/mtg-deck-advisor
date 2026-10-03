# Retrieval comparison: `baseline` → `instr-none`, 2026-10-02

Dev set (44 card queries, 45 rules questions). Baseline: As shipped in #70: cards hybrid, rules vector. Candidate: No query instruction, for cards or rules. Script: `scripts/retrieval_experiments.py`.

Deltas are in points (candidate minus baseline). The 95% intervals are bootstrap intervals over queries: with this few queries they're wide, and an interval that includes 0 means the change could be noise.

| Set | queries | recall@10 | MRR@10 | wins / losses / ties |
|---|---|---|---|---|
| Cards | 44 | 0.58 → 0.49 (-8.3, 95% [-16.8, -1.3]) | 0.45 → 0.39 (-5.9, 95% [-17.3, +5.3]) | 13 / 13 / 18 |
| Rules | 45 | 0.97 → 0.86 (-11.1, 95% [-20.0, -2.2]) | 0.70 → 0.58 (-12.1, 95% [-20.1, -4.6]) | 3 / 16 / 26 |

## Adoption rule

**Cards: do not adopt.** Candidate latency: median 90 ms, p90 320 ms.

- FAIL: best gain -5.9 points (needs +3)
- FAIL: 13 wins, 13 losses (needs more wins)
- FAIL: worst change -8.3 points (may drop at most 2)
- pass: median search 90 ms (budget 2 s)

**Rules: do not adopt.** Candidate latency: median 16 ms, p90 17 ms.

- FAIL: best gain -11.1 points (needs +3)
- FAIL: 3 wins, 16 losses (needs more wins)
- FAIL: worst change -12.1 points (may drop at most 2)
- pass: median search 16 ms (budget 2 s)

## By group (recall@10, MRR@10)

| Group | recall@10 | MRR@10 |
|---|---|---|
| cards, catalogue (23) | 0.59 → 0.43 | 0.42 → 0.29 |
| cards, `paraphrase` (18) | 0.47 → 0.36 | 0.32 → 0.31 |
| cards, `term` (3) | 1.00 → 0.47 | 0.61 → 0.13 |
| cards, `name` (2) | 1.00 → 1.00 | 1.00 → 0.33 |
| cards, pool (21) | 0.57 → 0.57 | 0.48 → 0.50 |
| cards, `need` (21) | 0.57 → 0.57 | 0.48 → 0.50 |
| rules, `commander` (14) | 0.93 → 0.79 | 0.59 → 0.47 |
| rules, `keyword` (16) | 0.97 → 0.78 | 0.70 → 0.47 |
| rules, `general` (15) | 1.00 → 1.00 | 0.81 → 0.80 |

## Queries that changed

| Query | | recall@10 | reciprocal rank |
|---|---|---|---|
| C04 green sorcery that finds two basic lands, one onto the battlefield tapped and one into your hand | loss | 0.25 → 0.00 | 0.25 → 0.00 |
| C05 four-mana sorcery that destroys all creatures, and they can't be regenerated | win | 0.50 → 0.50 | 0.33 → 1.00 |
| C08 equipment that draws two cards when the equipped creature dies | win | 1.00 → 1.00 | 0.20 → 1.00 |
| C09 a land that taps for any color in your commander's color identity | loss | 0.75 → 0.25 | 1.00 → 0.50 |
| C10 an enchantment that doubles the tokens you create | loss | 0.50 → 0.25 | 1.00 → 0.10 |
| C16 bounce a nonland permanent you don't control, or overload it to bounce all of them | loss | 1.00 → 0.00 | 0.10 → 0.00 |
| C19 ripple | loss | 1.00 → 0.40 | 0.50 → 0.14 |
| C20 epic | loss | 1.00 → 0.00 | 0.33 → 0.00 |
| C21 gravestorm | loss | 1.00 → 1.00 | 1.00 → 0.25 |
| C22 Atraxa | loss | 1.00 → 1.00 | 1.00 → 0.33 |
| C23 kodamas reach | loss | 1.00 → 1.00 | 1.00 → 0.33 |
| P01 counterspells | win | 0.80 → 1.00 | 1.00 → 1.00 |
| P02 ways to destroy an artifact or enchantment | win | 0.25 → 0.25 | 0.25 → 1.00 |
| P03 cheap ramp | win | 0.00 → 0.17 | 0.00 → 0.12 |
| P04 repeatable card draw | win | 0.33 → 0.33 | 0.17 → 0.25 |
| P07 get cards back from my graveyard to my hand | win | 0.22 → 0.22 | 0.33 → 1.00 |
| P08 make Treasure tokens | win | 1.00 → 1.00 | 0.50 → 1.00 |
| P10 cheap burn to kill a creature | win | 0.25 → 0.25 | 0.10 → 0.14 |
| P13 lands that tap for two colors | win | 1.00 → 1.00 | 0.33 → 0.50 |
| P15 equipment that gives haste | loss | 1.00 → 1.00 | 1.00 → 0.50 |
| P16 stop opposing creatures from blocking | win | 0.17 → 0.33 | 0.17 → 0.20 |
| P17 mill an opponent | win | 0.50 → 0.75 | 1.00 → 0.25 |
| P18 tap down opposing creatures | loss | 1.00 → 0.67 | 0.50 → 0.14 |
| P19 flicker a creature: exile it and return it to the battlefield | win | 1.00 → 1.00 | 0.25 → 0.50 |
| P20 take an extra turn | loss | 0.50 → 0.50 | 1.00 → 0.50 |
| P21 copy my instant and sorcery spells | loss | 1.00 → 0.50 | 0.17 → 0.11 |
| R01 How many cards must a Commander deck have? | win | 1.00 → 1.00 | 0.33 → 0.50 |
| R06 What life total do players start with in Commander? | loss | 1.00 → 0.00 | 0.10 → 0.00 |
| R09 Can my commander go to the command zone instead of my hand or library? | win | 1.00 → 1.00 | 0.33 → 0.50 |
| R10 How much commander damage makes a player lose? | loss | 1.00 → 1.00 | 1.00 → 0.50 |
| R11 Can a Vehicle be my commander? | loss | 1.00 → 1.00 | 0.50 → 0.25 |
| R12 A planeswalker says it can be your commander. Is that allowed? | loss | 1.00 → 0.00 | 0.33 → 0.00 |
| R13 Where does my commander start the game? | loss | 1.00 → 1.00 | 1.00 → 0.17 |
| R15 How do partner commanders work? | loss | 1.00 → 1.00 | 0.50 → 0.33 |
| R17 What does deathtouch do? | loss | 1.00 → 1.00 | 0.25 → 0.17 |
| R19 How does lifelink work? | loss | 1.00 → 0.00 | 0.25 → 0.00 |
| R22 When can I cast a spell with flash? | loss | 1.00 → 1.00 | 0.33 → 0.17 |
| R24 How does cascade work? | loss | 1.00 → 0.00 | 0.50 → 0.00 |
| R28 What happens to a permanent when it phases out? | loss | 0.50 → 0.50 | 1.00 → 0.25 |
| R29 How does ninjutsu work? | loss | 1.00 → 0.00 | 1.00 → 0.00 |
| R30 What does proliferate do? | loss | 1.00 → 1.00 | 1.00 → 0.50 |
| R31 How does double strike deal damage in combat? | loss | 1.00 → 1.00 | 0.50 → 0.33 |
| R33 What happens to a creature with 0 toughness? | loss | 1.00 → 1.00 | 0.50 → 0.33 |
| R37 What happens to a token that leaves the battlefield? | win | 1.00 → 1.00 | 0.50 → 1.00 |
| R39 What is the mana value of a card with X in its cost? | loss | 1.00 → 1.00 | 1.00 → 0.50 |
