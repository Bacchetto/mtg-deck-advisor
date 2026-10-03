# Retrieval experiments without an LLM at search time (#73), 2026-10-02

All results are on the **dev** set: 44 card queries and 45 rules questions. The held-out test set wasn't touched; it's scored once, for the final configuration, in #76.

Each comparison has its own report in this folder (`*-compare-<baseline>-vs-<candidate>.md`), with per-query changes. Script: `scripts/retrieval_experiments.py`.

**The adoption rule, fixed before any experiment.** A candidate replaces the current best only if:
- recall@10 or MRR@10 improves by at least 3 points
- it wins more queries than it loses
- neither metric drops by more than 2 points
- the median search takes at most 2 s

## Results

Values are recall@10 / MRR@10 for cards. Brackets are 95% bootstrap intervals for the change, in points.

| Step | Compared with | Cards | W / L | Rules | Verdict |
|---|---|---|---|---|---|
| Baseline (as shipped: 0.6b, hybrid, keyword weight 1) | | 0.58 / 0.45 | | 0.97 / 0.70 | |
| Vector only | baseline | 0.53 / 0.49 (−5.1 [−13.9, +3.5]) | 14 / 16 | same | no |
| Instruction "search by name, mechanic or effect" | baseline | 0.56 / 0.46 | 8 / 7 | 0.97 / 0.68 | no |
| Instruction "what a deck needs" | baseline | 0.57 / 0.46 | 8 / 5 | same | no |
| No query instruction | baseline | 0.49 / 0.39 (−8.3 [−16.8, −1.3]) | 13 / 13 | 0.86 / 0.58 | no: the instruction matters |
| Keyword arm only after all-words matches | baseline | 0.59 / 0.46 | 5 / 1 | same | no |
| Fusion depth 30 | baseline | 0.61 / 0.45 (+3.1 [−1.8, +9.1]) | 9 / 2 | same | passes, but depth 100 also gains: likely noise |
| Fusion depth 100 | baseline | 0.60 / 0.45 | 6 / 0 | same | no |
| **Keyword arm at half weight** | baseline | **0.62 / 0.50** (+4.0 [+0.6, +9.7]; +5.3 [+1.6, +10.0]) | 13 / 3 | same | **adopt** |
| Half weight + depth 30 | half weight | 0.61 / 0.50 | 5 / 3 | same | no |
| Half weight + depth 100 | half weight | 0.59 / 0.51 | 3 / 3 | same | no |
| `qwen3-embedding:4b` (half weight) | half weight | 0.63 / 0.58 (MRR +8.5 [+0.5, +16.6]) | 18 / 9 | 0.98 / 0.76 | adopt |
| **`qwen3-embedding:8b` (half weight)** | half weight | **0.72 / 0.63** (+10.6 [+3.7, +18.6]; +13.4 [+5.1, +22.0]) | 23 / 6 | **1.00 / 0.76** | **adopt** |
| 8b against 4b | 4b | 0.72 / 0.63 against 0.63 / 0.58 | 20 / 7 | tie | 8b over 4b |
| 8b, keyword at full weight | 8b | 0.69 / 0.62 (−3.3 [−6.9, −0.1]) | 4 / 14 | same | no |
| 8b, vector only | 8b | 0.75 / 0.65 (+2.8 [−3.0, +9.3]) | 12 / 11 | same | no: within noise, hybrid kept |

A rebuild of the baseline through the script's own fusion code matched it exactly (0 / 0 / 44 ties), so the reimplementation is faithful.

## Best configuration without an LLM

- **Embedding model:** `qwen3-embedding:8b`, truncated to its first 1,024 dimensions and renormalised. Qwen3 embeddings are Matryoshka-trained, so the schema's `vector(1024)` and its HNSW indexes stay as they are.
- **Cards:** hybrid search, with the keyword arm at weight 0.5 in the fusion.
- **Rules:** vector search.
- **Query instructions:** unchanged (ADR 0009's card instruction).

| Dev set | Baseline | Best | Change |
|---|---|---|---|
| Cards, recall@10 / MRR@10 | 0.58 / 0.45 | **0.72 / 0.63** | +14 / +18 points |
| Rules, recall@10 / MRR@10 | 0.97 / 0.70 | **1.00 / 0.76** | +3 / +6 points |
| Median card search | 91 ms | 207 ms | within the 2 s budget |

## Costs of the bigger model

- **Embedding time:** 4b embedded all 35,282 cards and rules in 15 min (38.6 per second, 4.4 GB VRAM). 8b runs at 29.5 per second (6.6 GB VRAM), about 20 min. The 0.6b model took 7.5 min. A full re-embed only happens when the model or text version changes; ingestion re-embeds only changed cards.
- **Query latency** is about 120 ms more per search (embedding the query with a bigger model).
- **VRAM is the real constraint.** 8b (6.6 GB) and the local chat model `qwen3:14b` (9.6 GB) together exceed the GPU's 16 GB. Any local-LLM step at search time (#75) has to fit beside the embedder, or accept models being swapped. That's measured in #75.

## Caveats

- **Many comparisons on one small set.** About 15 comparisons were run on the same 44 card queries, so one could pass the rule by chance. That's why marginal passes (depth 30) weren't adopted when a related variant contradicted them, and why the final configuration is scored on the held-out set before anything is claimed.
- **Measurement incidents, both caught and fixed.**
  - The first 8b embedding run crashed on an Ollama socket error after 1,000 cards, and a run against that partial table produced a meaningless score. Variants on other models now refuse to run unless every card and rule is embedded.
  - The embed command's batches weren't committing individually (an open read transaction made them savepoints), so it wasn't really resumable. Also fixed.
