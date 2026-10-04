# Retrieval comparison: `rerank-8b` → `production-reranked`, 2026-10-04

Dev set (44 card queries, 45 rules questions). Baseline: As rerank-14b, with qwen3:8b reranking: it fits beside the 8b embedder in VRAM. Candidate: The app's own search_cards with its reranker (RERANK_MODEL), and search_rules: what #76 ships. Script: `scripts/retrieval_experiments.py`.

Deltas are in points (candidate minus baseline). The 95% intervals are bootstrap intervals over queries: with this few queries they're wide, and an interval that includes 0 means the change could be noise.

| Set | queries | recall@10 | MRR@10 | wins / losses / ties |
|---|---|---|---|---|
| Cards | 44 | 0.81 → 0.81 (-0.4, 95% [-4.0, +3.0]) | 0.83 → 0.83 (+0.0, 95% [+0.0, +0.0]) | 2 / 2 / 40 |
| Rules | 45 | 0.97 → 1.00 (+3.3, 95% [+0.0, +8.9]) | 0.84 → 0.76 (-7.5, 95% [-14.6, -1.5]) | 3 / 9 / 33 |

## Adoption rule

**Cards: do not adopt.** Candidate latency: median 1224 ms, p90 1614 ms.

- FAIL: best gain +0.0 points (needs +3)
- FAIL: 2 wins, 2 losses (needs more wins)
- pass: worst change -0.4 points (may drop at most 2)
- pass: median search 1224 ms (budget 2 s)

**Rules: do not adopt.** Candidate latency: median 38 ms, p90 43 ms.

- pass: best gain +3.3 points (needs +3)
- FAIL: 3 wins, 9 losses (needs more wins)
- FAIL: worst change -7.5 points (may drop at most 2)
- pass: median search 38 ms (budget 2 s)

## By group (recall@10, MRR@10)

| Group | recall@10 | MRR@10 |
|---|---|---|
| cards, catalogue (23) | 0.86 → 0.84 | 0.82 → 0.82 |
| cards, `paraphrase` (18) | 0.82 → 0.79 | 0.80 → 0.80 |
| cards, `term` (3) | 1.00 → 1.00 | 0.83 → 0.83 |
| cards, `name` (2) | 1.00 → 1.00 | 1.00 → 1.00 |
| cards, pool (21) | 0.76 → 0.78 | 0.84 → 0.84 |
| cards, `need` (21) | 0.76 → 0.78 | 0.84 → 0.84 |
| rules, `commander` (14) | 0.89 → 1.00 | 0.77 → 0.67 |
| rules, `keyword` (16) | 1.00 → 1.00 | 0.93 → 0.88 |
| rules, `general` (15) | 1.00 → 1.00 | 0.80 → 0.72 |

## Queries that changed

| Query | | recall@10 | reciprocal rank |
|---|---|---|---|
| C13 equipment that gives a creature haste and stops it being targeted | loss | 1.00 → 0.50 | 1.00 → 1.00 |
| P05 board wipes that kill every creature | loss | 0.86 → 0.57 | 1.00 → 1.00 |
| P07 get cards back from my graveyard to my hand | win | 0.33 → 0.44 | 1.00 → 1.00 |
| P19 flicker a creature: exile it and return it to the battlefield | win | 0.50 → 1.00 | 1.00 → 1.00 |
| R02 Can I run more than one copy of a card in Commander? | win | 0.00 → 1.00 | 0.00 → 0.11 |
| R08 What happens when my commander would die or be exiled? | loss | 1.00 → 1.00 | 1.00 → 0.50 |
| R09 Can my commander go to the command zone instead of my hand or library? | loss | 1.00 → 1.00 | 1.00 → 0.50 |
| R10 How much commander damage makes a player lose? | win | 0.50 → 1.00 | 0.50 → 0.50 |
| R12 A planeswalker says it can be your commander. Is that allowed? | loss | 1.00 → 1.00 | 0.50 → 0.33 |
| R13 Where does my commander start the game? | loss | 1.00 → 1.00 | 0.50 → 0.20 |
| R22 When can I cast a spell with flash? | loss | 1.00 → 1.00 | 1.00 → 0.25 |
| R35 When does a player lose the game for having no life? | loss | 1.00 → 1.00 | 0.50 → 0.33 |
| R37 What happens to a token that leaves the battlefield? | win | 1.00 → 1.00 | 0.50 → 1.00 |
| R40 How many lands can I play each turn? | loss | 1.00 → 1.00 | 1.00 → 0.25 |
| R42 Which spell on the stack resolves first? | loss | 1.00 → 1.00 | 0.50 → 0.17 |
| R43 How do extra turns work? | loss | 1.00 → 1.00 | 1.00 → 0.50 |
