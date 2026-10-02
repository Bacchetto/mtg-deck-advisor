# 0008 - One model interface, owned by the project

**Status:** Accepted, 2026-10-02
**Applies to:** `mtg_deck_advisor.llm`

## Context

The project will call models for card role tagging (Milestone 3), cited answers (Milestone 4), the deck-building agent (Milestone 5), and model-graded evals (Milestone 7). The requirements ask for:

- **MOD-1:** all model calls through one internal interface
- **MOD-2:** structured output validated against a schema; invalid output retried or rejected, never passed through
- **MOD-3:** a timeout, retry policy and token budget on every call
- **MOD-4:** providers swappable by configuration, including a hosted API and a local model
- **OBS-1 / OBS-2:** every call logged with arguments, tokens, cost, latency and outcome, and linked by trace ID

Three providers are planned: Claude through the Anthropic SDK, a local model through Ollama, and recorded responses for no-key demos and deterministic CI.

## Decision

- **The project's own types** (`ModelRequest`, `ProviderResponse`, `Usage`) and a small **`Provider` protocol** with one method, `complete(request, model)`. Every provider returns plain text and token usage, nothing provider-specific.
- **One `ModelClient`** wraps the configured provider. It's the only thing the rest of the app calls, and it adds what every call needs:
  - **Validation in one place.** `generate_structured(request, Schema)` sends the schema's JSON Schema to the provider, validates the reply with Pydantic, and on failure retries **once**, showing the model its invalid answer and the specific validation errors. A second failure raises `InvalidOutputError`, so invalid output is never passed on.
  - **A cost cap checked before each call** against its worst case: the estimated input plus all of `max_tokens` as output, at the model's price. A call that could exceed the run's cap is refused without being made.
  - **Cost from actual usage** and a pinned price table (`llm/pricing.py`). An unpriced model on a billing provider is rejected when the client is built, not discovered later as a silent $0.
  - **Distinct errors** for the different failures: `ModelCallError` (provider or network), `RefusalError` (`stop_reason: refusal`), `InvalidOutputError`, `BudgetExceededError`.
  - **A record of every provider call**, retries included, as a structlog line and a `model_calls` row. The row holds the full request JSON, response text, tokens, cost, latency, outcome and trace ID, so a request can be reconstructed from its trace ID.
- **Timeouts and transport retries belong to each provider** (the Anthropic SDK retries 429 and 5xx itself), configured from settings. `ModelClient` retries only the one thing no SDK knows about: output that fails *our* schema.
- **Tests never make paid calls (ENG-2).** Unit tests use a scripted `FakeProvider`. Tests that call real services are marked `live` and skipped unless pytest runs with `--live`, which CI never does.

## Alternatives

**Use the Anthropic SDK directly everywhere.** It's the simplest start, but provider swapping (MOD-4) would mean touching every call site, and validation, budgets and logging would be repeated or forgotten at each one.

**An LLM framework** (LangChain, LiteLLM, ...). Provider abstraction comes ready-made, but it adds a large dependency whose behaviour (retries, prompt formatting, structured output) sits between the project and the API, and is harder to explain and to test. The project needs one method and four implementations. AGT-6 separately allows comparing a framework-based agent later, if wanted.

**Let each provider validate structured output natively** (e.g. the SDK's parse helpers). Native constrained decoding is still used by providers that support it, through the JSON Schema passed in. But *acceptance* is decided once, in `ModelClient`, so "invalid output is never passed through" doesn't depend on which provider is configured.

**Retry invalid output several times.** Each retry costs money and usually fails the same way. One corrective retry, with the errors shown, fixes the common slip; anything beyond that is a prompt or schema problem to surface, not to loop on.

## Consequences

- Swapping between `anthropic`, `ollama` and `replay` is a settings change (`MODEL_PROVIDER`, `MODEL_NAME`).
- Every call, including refused, invalid and budget-blocked ones, is visible in `model_calls`, and cost can be summed by purpose, model or trace.
- **The budget check is conservative.** It assumes the full `max_tokens` will be used, so a cap near the actual spend may refuse a call that would have fitted. It never lets one through that could break the cap, which is the property that matters.
- **The input estimate is rough** (about 4 characters per token). It's used only for that check, never for billing; billing uses the provider's reported usage.
- Prices are maintained by hand in one pinned table; a test makes any change deliberate.
