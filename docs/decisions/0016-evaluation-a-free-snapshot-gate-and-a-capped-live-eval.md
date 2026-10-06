# 0016 - Evaluation: a free snapshot gate on every PR, and a capped live eval on main

**Status:** Accepted, 2026-10-06
**Applies to:** `mtg_deck_advisor.evaluation`, `evals/`, `.github/workflows/ci.yml`

## Context

Milestone 7 needs a regression eval in CI that fails the build when results drop (EVL-5). The milestone's done-when is that a deliberately degraded prompt or retrieval setting makes it fail.

Two things stand in the way:
- **CI can't run the local models.** Card and rule search embed every query with `qwen3-embedding:8b`, an 8B-parameter model run on the owner's GPU, and the card search reranks with `qwen3:8b`. GitHub's runners have no GPU, and their database starts empty.
- **Model calls cost money.** Running the paid agent on every pull request would cost a few cents each time and give results that vary from run to run.

## Decision

**The eval runs in two tiers.**

### Tier 1: a free, deterministic gate on every PR (#119)
- **An embedding snapshot,** committed under `evals/snapshot/`, about 19 MB. It's exported locally from the full database with `python -m mtg_deck_advisor.evaluation.snapshot` and holds:
  - **4,122 cards:** every labelled card, the three committed pools, the validator decks' cards, and 3,000 distractors (random Commander-legal cards, chosen by a salted hash, so the same every time), each with its embedding. Pool cards also have their summary embedding.
  - **Every current rule** (3,166), with its embedding.
  - **The 152 eval queries,** embedded as search embeds them. A `SnapshotEmbedder` answers for the model, and refuses a text it has no vector for, which means the snapshot needs exporting again.
  - **Vectors in half precision,** as base64. Similarities move by about 0.001, so near-ties can swap places.
- **`python -m mtg_deck_advisor.evaluation.gate`** loads the snapshot into a throwaway pgvector container, the same way the integration tests start theirs, and scores:
  - **Retrieval:** every dev and test card query and rules question, searched as the app searches by default (hybrid cards, vector rules, no reranker). Recall@10 and MRR@10.
  - **The validator:** 12 labelled decks (`evals/datasets/validator_decks.jsonl`), each changed from the recorded Adeline draft. Each must get exactly its expected violation codes.
  - **Pool resolution:** every entry of the three committed pools must resolve.
- **Thresholds** in `evals/thresholds.toml`. The scores are deterministic, so retrieval thresholds sit 0.02 below the snapshot baseline; the validator and resolution must be 1.0.
- **Checked:** switching the default card search to keyword-only fails four metrics (dev recall@10 falls from 0.84 to 0.52).
- **The real model, optionally:** a manual CI run with `ollama: true` installs Ollama on the runner and pulls the model. It embeds the queries for real, requires each to match the snapshot's vector (cosine 0.99 or higher), and scores retrieval with the live vectors.

### Tier 2: a capped live eval on main and on demand (#124)
- A few rules questions and a small deck task on the real agent, run on pushes to `main` and manual runs, never on PRs. Each run is capped by the client's cost cap.
- During a case, searches use the snapshot embedding of that case's question, since the model's own queries can't be embedded without Ollama.
- Thresholds come from a baseline run, with slack for model variance.

### The full evals run locally
The rules Q&A set, the deck tasks, rubric grading, injection cases and variant comparisons run on the owner's machine, with the local models. Each paid run is estimated and approved first. Results are saved under `evals/runs/` and reported, dated, under `evals/reports/`.

## Consequences

- **Every PR is checked for free in about a minute.** A retrieval or validator regression fails the build before merge.
- **The snapshot's scores aren't the product's scores.** The snapshot holds about 4,100 cards instead of 32,000 and runs without the reranker, so its numbers are only compared with its own baseline.
- **The snapshot has to be kept current.** A change to the embedded text, the embedding model, the rules, or a labelled query means exporting it again (about 40 s locally) and re-baselining in the same PR. A new query fails loudly until the export is done.
- **About 19 MB of binary-ish data is in git.** Each re-export adds about that much to the history, so re-export only for a reason.
- **Prompt changes are caught only on `main`,** by the live tier, after merge. That's a deliberate trade for not paying per PR push.

## Alternatives

- **Ollama on every CI run:** a 5 GB model download (cached) and CPU embedding on every PR. Slow and heavy, and it would still need the full card embeddings in the CI database. It's kept as the optional manual check.
- **Keyword search only in CI:** no embeddings needed, but a vector or fusion regression would pass.
- **A self-hosted runner on the owner's GPU machine:** this is a public repository, so pull requests could run code on that machine.
