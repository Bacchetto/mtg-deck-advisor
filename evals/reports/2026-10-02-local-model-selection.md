# Local model selection, 2026-10-02

Produced by `scripts/measure_local_models.py` on the development machine: Ryzen 5 7600, 31 GB RAM, AMD Radeon RX 9070 XT (16 GB), Ollama 0.35.0 (ROCm), Windows 11. Card data: Scryfall oracle cards, 2026-10-01. Local models are free to run.

These are model-selection checks, deliberately small. The proper retrieval evaluation is Milestone 4 (EVL-1), and the proper role-tagging comparison is issue #51.

## Embeddings

20 plain-English queries, each describing a real card's effect in words different from its rules text (for example "an artifact that taps for two colorless mana" for Sol Ring), searched against 2,000 random Commander-legal cards plus the 20 targets. Each model uses its own documented query and document prefixes. Document text is `name. type line. rules text`.

**As stored (symbols like `{T}: Add {C}{C}`):**

| Model | Dims | recall@1 | recall@10 | MRR | cards/s |
|---|---|---|---|---|---|
| nomic-embed-text | 768 | 0.30 | 0.50 | 0.363 | 100 |
| mxbai-embed-large | 1024 | 0.30 | 0.70 | 0.435 | 60 |
| qwen3-embedding:0.6b | 1024 | 0.35 | 0.60 | 0.460 | 96 |

**With mana and tap symbols written out as words (`--expand-symbols`):**

| Model | Dims | recall@1 | recall@10 | MRR | cards/s |
|---|---|---|---|---|---|
| nomic-embed-text | 768 | 0.35 | 0.55 | 0.401 | 99 |
| mxbai-embed-large | 1024 | 0.40 | 0.75 | 0.516 | 61 |
| qwen3-embedding:0.6b | 1024 | 0.40 | 0.70 | 0.497 | 98 |

**Findings:**
- **Expanding the symbols improved every model.** Sol Ring went from rank 500 to 1,000 in every model to inside the top 10.
- **Some queries stay hard for all models.** Control Magic ("Enchant creature / You control enchanted creature" for "steal a creature") and Lightning Greaves miss everywhere. That's a case for hybrid search (RAG-5) and for adding role tags to embedded text.
- **mxbai-embed-large and qwen3-embedding:0.6b are within one query of each other,** which is noise on 20 queries.

## Chat (role tagging sanity check)

12 cards whose main role is beyond dispute (Sol Ring: ramp, Wrath of God: board wipe, ...), using a role-tagging prompt with a JSON-schema output (12 roles). Each model was measured **with every other model unloaded first**.

| Model | First load | Per card, warm | Valid JSON | Obvious role found | VRAM |
|---|---|---|---|---|---|
| qwen3:8b | 0.7 s | 0.56 s | 12/12 | 11/12 | 5.6 GB |
| gemma3:12b | 13.6 s | 0.36 s | 12/12 | 12/12 | 8.0 GB |
| qwen3:14b | 14.7 s | 0.28 s | 12/12 | 12/12 | 9.6 GB |

**A measurement pitfall, found and fixed.** The first run measured each model with the previous ones still loaded. gemma3:12b and qwen3:14b then took about 2.4 s per card, roughly 10 times slower. Ollama reported all three models as 100% in VRAM, totalling 23.2 GB on a 15.9 GB card: the AMD driver on Windows extends GPU memory into much slower shared system RAM, and Ollama counts that as VRAM. The script now unloads every model before measuring the next.

## Decision

- **Embeddings: `qwen3-embedding:0.6b`.** It's level with mxbai-embed-large on quality, 60% faster, and reads 32k tokens of input where mxbai reads 512. The longest Comprehensive Rules entry is about 700 tokens, which mxbai would silently truncate.
- **Local chat: `qwen3:14b`.** It ties for the best quality on this check, is fastest once warm, supports tool calling, and fits on the GPU alongside the embedding model.

See ADR 0009.
