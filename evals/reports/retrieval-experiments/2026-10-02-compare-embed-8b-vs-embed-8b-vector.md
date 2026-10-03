# Retrieval comparison: `embed-8b` → `embed-8b-vector`, 2026-10-02

Dev set (44 card queries, 45 rules questions). Baseline: qwen3-embedding:8b truncated to 1,024 dimensions; cards hybrid with the keyword arm at 0.5 (the current best), rules vector. Candidate: qwen3-embedding:8b, vector search only (no keyword arm). Script: `scripts/retrieval_experiments.py`.

Deltas are in points (candidate minus baseline). The 95% intervals are bootstrap intervals over queries: with this few queries they're wide, and an interval that includes 0 means the change could be noise.

| Set | queries | recall@10 | MRR@10 | wins / losses / ties |
|---|---|---|---|---|
| Cards | 44 | 0.72 → 0.75 (+2.8, 95% [-3.0, +9.3]) | 0.63 → 0.65 (+1.7, 95% [-8.9, +12.1]) | 12 / 11 / 21 |
| Rules | 45 | 1.00 → 1.00 (+0.0, 95% [+0.0, +0.0]) | 0.76 → 0.76 (+0.0, 95% [+0.0, +0.0]) | 0 / 0 / 45 |

## Adoption rule

**Cards: do not adopt.** Candidate latency: median 203 ms, p90 460 ms.

- FAIL: best gain +2.8 points (needs +3)
- pass: 12 wins, 11 losses (needs more wins)
- pass: worst change +1.7 points (may drop at most 2)
- pass: median search 203 ms (budget 2 s)

**Rules: do not adopt.** Candidate latency: median 55 ms, p90 65 ms.

- FAIL: best gain +0.0 points (needs +3)
- FAIL: 0 wins, 0 losses (needs more wins)
- pass: worst change +0.0 points (may drop at most 2)
- pass: median search 55 ms (budget 2 s)

## By group (recall@10, MRR@10)

| Group | recall@10 | MRR@10 |
|---|---|---|
| cards, catalogue (23) | 0.77 → 0.83 | 0.57 → 0.59 |
| cards, `paraphrase` (18) | 0.71 → 0.78 | 0.48 → 0.54 |
| cards, `term` (3) | 1.00 → 1.00 | 0.83 → 0.83 |
| cards, `name` (2) | 1.00 → 1.00 | 1.00 → 0.62 |
| cards, pool (21) | 0.67 → 0.67 | 0.70 → 0.72 |
| cards, `need` (21) | 0.67 → 0.67 | 0.70 → 0.72 |
| rules, `commander` (14) | 1.00 → 1.00 | 0.67 → 0.67 |
| rules, `keyword` (16) | 1.00 → 1.00 | 0.88 → 0.88 |
| rules, `general` (15) | 1.00 → 1.00 | 0.72 → 0.72 |

## Queries that changed

| Query | | recall@10 | reciprocal rank |
|---|---|---|---|
| C04 green sorcery that finds two basic lands, one onto the battlefield tapped and one into your hand | loss | 0.25 → 0.25 | 0.50 → 0.33 |
| C05 four-mana sorcery that destroys all creatures, and they can't be regenerated | win | 1.00 → 1.00 | 0.50 → 1.00 |
| C06 counter target spell for two blue mana | win | 0.00 → 1.00 | 0.00 → 0.11 |
| C07 black sorcery that searches your library for any card and puts it into your hand | win | 0.00 → 0.50 | 0.00 → 0.33 |
| C08 equipment that draws two cards when the equipped creature dies | loss | 1.00 → 1.00 | 1.00 → 0.25 |
| C09 a land that taps for any color in your commander's color identity | loss | 1.00 → 0.75 | 1.00 → 1.00 |
| C13 equipment that gives a creature haste and stops it being targeted | win | 1.00 → 1.00 | 0.33 → 0.50 |
| C14 whenever an opponent casts a spell, you draw a card unless they pay one | win | 1.00 → 1.00 | 0.33 → 1.00 |
| C15 whenever an opponent draws a card, you get a Treasure unless they pay two | win | 1.00 → 1.00 | 0.17 → 0.50 |
| C16 bounce a nonland permanent you don't control, or overload it to bounce all of them | win | 1.00 → 1.00 | 0.25 → 0.50 |
| C17 a land that gives you no maximum hand size | loss | 1.00 → 1.00 | 0.50 → 0.20 |
| C23 kodamas reach | loss | 1.00 → 1.00 | 1.00 → 0.25 |
| P04 repeatable card draw | win | 0.33 → 0.33 | 0.20 → 0.25 |
| P05 board wipes that kill every creature | win | 0.43 → 0.57 | 1.00 → 1.00 |
| P07 get cards back from my graveyard to my hand | loss | 0.33 → 0.33 | 1.00 → 0.25 |
| P09 give my creature hexproof, shroud or ward | win | 0.50 → 1.00 | 0.50 → 1.00 |
| P10 cheap burn to kill a creature | win | 0.75 → 0.75 | 0.33 → 1.00 |
| P12 tutor: search my library for a creature card | loss | 1.00 → 1.00 | 1.00 → 0.25 |
| P14 mana rocks that tap for any color | loss | 1.00 → 1.00 | 1.00 → 0.50 |
| P16 stop opposing creatures from blocking | win | 0.67 → 0.83 | 0.33 → 1.00 |
| P18 tap down opposing creatures | loss | 0.67 → 0.33 | 0.33 → 0.20 |
| P19 flicker a creature: exile it and return it to the battlefield | loss | 1.00 → 0.50 | 0.25 → 1.00 |
| P21 copy my instant and sorcery spells | loss | 1.00 → 1.00 | 0.50 → 0.33 |
