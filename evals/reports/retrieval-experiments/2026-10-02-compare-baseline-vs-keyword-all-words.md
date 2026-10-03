# Retrieval comparison: `baseline` → `keyword-all-words`, 2026-10-02

Dev set (44 card queries, 45 rules questions). Baseline: As shipped in #70: cards hybrid, rules vector. Candidate: Hybrid whose keyword arm ranks cards matching every word first, then any word. Script: `scripts/retrieval_experiments.py`.

Deltas are in points (candidate minus baseline). The 95% intervals are bootstrap intervals over queries: with this few queries they're wide, and an interval that includes 0 means the change could be noise.

| Set | queries | recall@10 | MRR@10 | wins / losses / ties |
|---|---|---|---|---|
| Cards | 44 | 0.58 → 0.59 (+1.7, 95% [+0.0, +4.0]) | 0.45 → 0.46 (+1.1, 95% [-3.0, +5.8]) | 5 / 1 / 38 |
| Rules | 45 | 0.97 → 0.97 (+0.0, 95% [+0.0, +0.0]) | 0.70 → 0.70 (+0.0, 95% [+0.0, +0.0]) | 0 / 0 / 45 |

## Adoption rule

**Cards: do not adopt.** Candidate latency: median 91 ms, p90 388 ms.

- FAIL: best gain +1.7 points (needs +3)
- pass: 5 wins, 1 losses (needs more wins)
- pass: worst change +1.1 points (may drop at most 2)
- pass: median search 91 ms (budget 2 s)

**Rules: do not adopt.** Candidate latency: median 15 ms, p90 17 ms.

- FAIL: best gain +0.0 points (needs +3)
- FAIL: 0 wins, 0 losses (needs more wins)
- pass: worst change +0.0 points (may drop at most 2)
- pass: median search 15 ms (budget 2 s)

## By group (recall@10, MRR@10)

| Group | recall@10 | MRR@10 |
|---|---|---|
| cards, catalogue (23) | 0.59 → 0.61 | 0.42 → 0.43 |
| cards, `paraphrase` (18) | 0.47 → 0.50 | 0.32 → 0.37 |
| cards, `term` (3) | 1.00 → 1.00 | 0.61 → 0.61 |
| cards, `name` (2) | 1.00 → 1.00 | 1.00 → 0.75 |
| cards, pool (21) | 0.57 → 0.58 | 0.48 → 0.48 |
| cards, `need` (21) | 0.57 → 0.58 | 0.48 → 0.48 |
| rules, `commander` (14) | 0.93 → 0.93 | 0.59 → 0.59 |
| rules, `keyword` (16) | 0.97 → 0.97 | 0.70 → 0.70 |
| rules, `general` (15) | 1.00 → 1.00 | 0.81 → 0.81 |

## Queries that changed

| Query | | recall@10 | reciprocal rank |
|---|---|---|---|
| C08 equipment that draws two cards when the equipped creature dies | win | 1.00 → 1.00 | 0.20 → 1.00 |
| C09 a land that taps for any color in your commander's color identity | win | 0.75 → 1.00 | 1.00 → 1.00 |
| C10 an enchantment that doubles the tokens you create | win | 0.50 → 0.75 | 1.00 → 1.00 |
| C23 kodamas reach | loss | 1.00 → 1.00 | 1.00 → 0.50 |
| P06 return a creature card from my graveyard to the battlefield | win | 0.25 → 0.50 | 0.25 → 0.25 |
| P21 copy my instant and sorcery spells | win | 1.00 → 1.00 | 0.17 → 0.33 |
