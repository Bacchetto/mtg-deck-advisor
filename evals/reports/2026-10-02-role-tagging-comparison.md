# Role tagging comparison, 2026-10-02

Gold sample: `evals\datasets\role_tagging_gold.csv`, 100 cards with owner-reviewed labels. Batches of 25, as in production. Taxonomy and prompt: `llm/roles.py`.

| Model | micro P | micro R | micro F1 | exact match | invalid batches | cost | per 1,000 cards | s/batch |
|---|---|---|---|---|---|---|---|---|
| claude-opus-5-5 | 0.89 | 0.98 | 0.93 | 0.90 | 0 | $0.119 | $1.19 | 10.4 |
| claude-sonnet-5-5 | 0.88 | 0.92 | 0.90 | 0.83 | 0 | $0.065 | $0.65 | 7.7 |
| claude-haiku-4-5 | 0.72 | 0.93 | 0.81 | 0.67 | 0 | $0.030 | $0.30 | 10.7 |
| qwen3:14b | 0.53 | 0.79 | 0.64 | 0.45 | 0 | $0.000 | $0.00 | 26.2 |

## F1 by role

| Role | support | claude-opus-5-5 | claude-sonnet-5-5 | claude-haiku-4-5 | qwen3:14b |
|---|---|---|---|---|---|
| ramp | 8 | 0.89 | 0.88 | 0.67 | 0.34 |
| card_draw | 13 | 1.00 | 0.92 | 0.84 | 0.81 |
| removal | 17 | 0.94 | 0.91 | 0.86 | 0.76 |
| board_wipe | 5 | 0.91 | 0.91 | 0.89 | 0.89 |
| counterspell | 3 | 1.00 | 1.00 | 1.00 | 1.00 |
| tutor | 3 | 1.00 | 1.00 | 1.00 | 0.75 |
| recursion | 4 | 1.00 | 0.80 | 0.80 | 0.73 |
| protection | 6 | 0.92 | 0.92 | 0.71 | 0.60 |
| token_maker | 11 | 0.95 | 1.00 | 0.96 | 0.64 |
| finisher | 5 | 0.67 | 0.50 | 0.50 | 0.50 |
| mana_fixing | 9 | 1.00 | 0.94 | 0.95 | 0.62 |
| land | 5 | 0.91 | 0.91 | 0.91 | 0.91 |
| mill | 1 | 1.00 | 1.00 | - | - |
| self_mill | 1 | 1.00 | 1.00 | - | - |

## Notes

- **Real spend for the run: $0.21** (Opus $0.119, Sonnet $0.065, Haiku $0.030). The estimate was $0.41, because output was smaller than assumed: Opus at `effort: low` produced about 39 output tokens per card, reasoning included.
- **Prompt caching worked on Opus and Sonnet,** with 3,840 cache-read tokens across the four batches. Haiku cached nothing; its minimum cacheable prompt is likely longer than this system prompt.
- **Haiku's responses named the model `claude-haiku-4-5-20251001`,** a dated alias missing from the price table. The calls were costed at the requested model's (identical) price, with a warning, and the alias is now priced.
- **`finisher` is the weakest role for every model** (F1 0.50 to 0.67). It's the most subjective definition and has only 5 examples. Some disagreements are defensible: Opus tags Kiki-Jiki as `token_maker finisher` ("enables infinite combos that end games"), where the gold label has `token_maker` only.
- **`mill` and `self_mill` have one example each,** so their scores mean little. They were added to the taxonomy during the owner's review.
- **The sample is small (100 cards).** A gap of 0.03 in F1 between Opus and Sonnet is a handful of cards.
- **All 16 responses are recorded** in `recordings/role_tagging/`, so the run can be re-scored for free (`MODEL_PROVIDER=replay`).

## Decision

**Claude Opus 5.5 tags card roles** (setting `ROLE_TAGGING_MODEL`), chosen by the owner. It has the best micro F1 (0.93) and exact match (0.90), and is best or tied on 12 of 14 roles. Tagging is on demand and cached per card, so the cost (about $1.19 per 1,000 newly seen cards) is paid once per card, not per use. The local `qwen3:14b` (F1 0.64) isn't used for tagging; it remains the free provider for demos.
