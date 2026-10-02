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
