# Recorded model responses

Model responses recorded from a real provider, replayed with `MODEL_PROVIDER=replay` for a
demo that needs no API key or local model, and for deterministic evals in CI. See
`src/mtg_deck_advisor/llm/replay.py`.

- **One file per request**, named by a SHA-256 of everything that could change the answer:
  model, system prompt, messages, `max_tokens`, effort and output schema. The purpose
  label isn't included, so renaming a purpose keeps its recordings.
- **Each file holds** the request, the response (text, stop reason, token usage, the model
  that answered), where it was recorded from, and when.
- **To record**, run with a real provider and `RECORD_RESPONSES=true`:

  ```bash
  MODEL_PROVIDER=anthropic RECORD_RESPONSES=true python -m ...
  ```

  Recording a paid provider costs what the calls cost; the cost cap applies as usual.
- **A request with no recording** fails with an error saying how to record it, rather than
  silently calling a real model.
- **Searches replay too.** The embedding model's query vectors are saved under
  `embeddings/` (one file per model and text), and the reranker's calls are recorded like
  any other. A replayed search then returns exactly what it returned when recorded, so the
  agent's next request, which carries that result, finds its recording.

## The demo session (`demo/`)

What the README's demo replays (`REPLAY_DIR=recordings/demo`, set by `.env.demo`). It was
recorded on 2026-10-08 with Sonnet 5.5, `qwen3-embedding:8b` and `qwen3:8b`, against a
database seeded from `demo/data/` (`python -m mtg_deck_advisor.demo.seed`), so a newcomer's
seeded database gives the same search results. It costs $0.16 to record again.

1. `mtg-advisor build`, the Demo collection, Enter to let the agent choose: a Sheoldred,
   Whispering One draft, approved.
2. Change: "Add more card draw and cut the weakest creatures.", approved, then exported.
3. `mtg-advisor ask` with three questions:
   - "My commander Sheoldred died and went to the graveyard. Can I move her to the command
     zone instead?"
   - "How much extra does it cost to cast my commander for the third time?"
   - "Can my mono-black deck include a card with a red hybrid mana symbol in its rules
     text?"

A change to a prompt, a tool, the dataset or the search code changes these requests, so
the session has to be recorded again, against a freshly seeded database.
