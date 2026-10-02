# 0009 - Local models, run natively and chosen by measurement

**Status:** Accepted, 2026-10-02
**Applies to:** `mtg_deck_advisor.llm.ollama`, settings `OLLAMA_*` and `EMBEDDING_MODEL`

## Context

MOD-4 requires a locally run model alongside the hosted one. Milestone 4's retrieval needs embeddings, and DEM-2 needs a demo that works without an API key. The development machine has an AMD Radeon RX 9070 XT (16 GB) and runs containers under Rancher Desktop on WSL2, where AMD GPU acceleration doesn't reach Docker containers.

Which models to use is an empirical question: the hardware, the domain's unusual text, and real latency all matter, and none can be settled from published benchmarks.

## Decision

- **Ollama runs natively on Windows**, not in Compose, so it can use the GPU (ROCm). The app reaches it at `http://127.0.0.1:11434`; containers use `http://host.docker.internal:11434`, set in Compose.
- **The models are chosen by measurement** (`scripts/measure_local_models.py`, results in `evals/reports/2026-10-02-local-model-selection.md`):
  - **Embeddings: `qwen3-embedding:0.6b`**, 1024 dimensions, normalised vectors. Its quality was level with `mxbai-embed-large` and well ahead of `nomic-embed-text`, it was 60% faster than mxbai, and it reads 32k tokens of input against mxbai's 512. That matters because the longest stored rule is about 700 tokens.
  - **Local chat: `qwen3:14b`.** It produced valid structured output on every card, tied for the best role accuracy, was fastest once warm (about 0.3 s per card), supports tool calling, and fits on the GPU (9.6 GB) beside the embedding model.
- **The provider's behaviour:**
  - temperature 0, for repeatable tags
  - thinking off unless high effort is asked for, so reasoning doesn't spend a classification's output budget
  - **no retries:** a local server that's down or a model that isn't pulled won't recover in seconds, so failures are immediate and say what to do
  - a 300-second timeout, because a first load of a 9 GB model is slow
- **The losing candidates were deleted** after measuring (`qwen3:8b`, `gemma3:12b`, `nomic-embed-text`, `mxbai-embed-large`).

## Alternatives

**Ollama in a Compose service.** One-command setup and better reproducibility, but CPU-only on this machine, several times slower. Compose could gain an optional CPU Ollama service later, for machines without a supported GPU.

**The best-known models by reputation.** `nomic-embed-text` is a common default; here it was the weakest by a clear margin on this domain's text.

**A hosted embedding API.** There's no Anthropic embedding API. A third-party one would add a second paid vendor and a key, and break the no-key demo (DEM-2) and free CI.

## Consequences

- **Embeddings are free and local.** Re-embedding the whole card table takes about 6 minutes on this machine.
- **Magic's symbol-heavy rules text** (`{T}: Add {C}{C}`) defeats every embedding model unless the symbols are written out as words. That raised every model's scores, and Milestone 4 adopts it when preparing text to embed. Some queries stay hard (Control Magic), which motivates hybrid keyword + vector search (RAG-5).
- **A measurement pitfall to remember:** on Windows the AMD driver extends GPU memory into shared system RAM, and Ollama reports that as VRAM. Models measured while others are loaded look up to 10 times slower. Measure with one model loaded, and keep the working set (about 10 GB) within real VRAM.
- **Model choice depends on this hardware.** A machine with less VRAM would pick smaller models through the same script. The model names are settings, not code.
- **These were selection checks** (20 queries, 12 cards). The proper evaluations are Milestone 4 (retrieval) and issue #51 (role tagging against Claude), and either may revise these choices.
