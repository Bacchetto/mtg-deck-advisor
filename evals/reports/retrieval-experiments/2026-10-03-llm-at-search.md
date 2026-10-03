# A local LLM at search time (#75), 2026-10-03

Two ways of using a local chat model inside a search, measured on the **dev** set against the best configuration from #74 (`summary-vector`):
- 8b embeddings and a half-weight keyword arm for all card searches
- summary vectors as a third arm for pool searches
- vector search for rules

The budget is a median of 2 s per search. Script: `scripts/retrieval_experiments.py`; there's one report per comparison in this folder.

- **Reranking:** the first stage's top 20 cards (or rules), with their real rules text, are given to the model, which returns the 10 that best answer the search, best first. Output is structured and validated. On an invalid answer, the first stage's order is kept.
- **HyDE:** the model writes the card (or rule) text the search describes, and its embedding is fused with the first stage by reciprocal rank.

## Results

| Variant | Cards: recall@10 / MRR@10 | W / L | Rules: recall@10 / MRR@10 | W / L | Median search |
|---|---|---|---|---|---|
| `summary-vector` (current best) | 0.78 / 0.64 | | 1.00 / 0.76 | | 0.15 s |
| Rerank, `qwen3:14b` | 0.80 / **0.87** (+22.1 [+12.6, +32.2]) | 17 / 3 | 0.99 / 0.87 | 12 / 3 | **9.3 s** / 10.3 s |
| **Rerank, `qwen3:8b`** | **0.81 / 0.83** (+3.7; **+18.7 [+9.5, +28.2]**) | **19 / 6** | 0.97 / 0.84 (recall −3.3) | 9 / 3 | **1.3 s** / 1.5 s |
| Rerank, `qwen3:4b` | 0.72 / 0.79 (recall −5.7) | 8 / 9 | **1.00 / 0.87** (+11.2 [+3.3, +20.0]) | 11 / 2 | 0.9 s / 0.9 s |
| HyDE, `qwen3:14b` | 0.69 / 0.62 (recall −8.3) | 13 / 16 | 1.00 / 0.76 | 4 / 5 | 4.5 s / 9.0 s |

**By the adoption rule:**
- **cards:** `qwen3:8b` reranking passes, the 14b fails on latency, and the 4b fails on recall
- **rules:** `qwen3:4b` reranking passes, and the 8b fails because recall drops 3.3 points

HyDE fails everywhere: it's worse and slow. Writing a hypothetical card pulls in cards like the model's guess, not the user's intent.

## Why 14b reranking took 9 s: GPU memory, not the model

| `qwen3:14b`, reranking 20 candidates (about 1,260 tokens in, 40 out) | Latency | Generation |
|---|---|---|
| Alone on the GPU | **1.4 s** | normal |
| Beside the 8b embedder | **8.8 s** | 6.5 tokens/s (normally about 55) |

The 14b (9.6 GB) and the 8b embedder (6.6 GB) together exceed the 16 GB card. On Windows, the AMD driver extends "VRAM" into shared system RAM, which Ollama reports as VRAM (ADR 0009). So the overflow ran from system memory and generation crawled. `qwen3:8b` (5.6 GB) beside the embedder fits in real VRAM, which is why it meets the budget.

## Decision for #76

- **Cards: rerank with `qwen3:8b`.** It's the largest quality gain of the improvement work: MRR 0.64 → 0.83, so the right card is usually first or second. It costs a median of 1.3 s per search.
- **Rules: no reranking.** The 4b passed for rules, but running two chat models beside the embedder (about 15.4 GB) brings back the memory spill. Rules are already at recall@10 1.00, and an answer step reads all 10. This is a deliberate choice of simplicity over +11 points of rules MRR; it can be revisited with more VRAM.

## Caveats

- **Many comparisons on one small set,** again. The held-out set (#76) is the honest check.
- **Reranking needs the local chat model running beside the embedder,** about 12 GB of VRAM. A machine without that falls back to unreranked results (production must make reranking optional, with the fallback tested).
- **Rerank latency depends on VRAM fitting.** If another model is loaded, searches slow down about 6 times. The agent in Milestone 5 uses Claude for its own reasoning, so that's not expected locally. But a local-only demo that also uses `qwen3:14b` for chat would hit it.
- **The 9 s versus 1.4 s diagnosis** was measured on 3 queries after a warm-up. The 8b and 4b runs were measured on all 89 dev queries, with only the embedder and the reranker loaded.
