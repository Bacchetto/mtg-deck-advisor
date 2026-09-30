# Project Plan

How the domain-agnostic requirements in [requirements.md](requirements.md) become a concrete product, and the order it will be built in.

## Product

**Proof of concept: a Commander deck builder.** A user submits the cards they own and names a commander. An AI agent drafts a legal 100-card Commander deck from that pool and explains its choices with citations. The user can then ask for refinements, such as "more removal" or "lower the curve."

The agent only *proposes* changes. Deterministic code validates every proposal, the user approves or rejects it, and every action is recorded in an audit log.

**Scope decisions:**

- The card pool is submitted as a plain text list (`1 Sol Ring`) or a CSV collection export.
- Basic lands are unlimited and never need to be in the pool.
- There is one hosted model provider (Claude) and one local provider (Ollama). A replay provider of recorded responses powers demo mode and CI.
- The flow is draft, then refine, with user approval of each proposed change.
- Interfaces are an HTTP API (FastAPI), a thin CLI, and an MCP server. There is no web UI in the proof of concept.
- The long documents for retrieval are the MTG Comprehensive Rules.

**Out of scope for the proof of concept:** partner and background commanders, companions, budget and power-level targeting, and multiple decks per request.

## How the product meets the requirements

| Area | Concrete form in this project |
|---|---|
| ING | Scryfall `oracle_cards` bulk data (external API, cached, rate-limited, content-hashed); Comprehensive Rules text; pool parsers for text and CSV. |
| RAG-1 | Semantic search over pool cards, with exact filters (color identity within the commander's, type, mana value, in pool). |
| RAG-2 | Comprehensive Rules chunked by rule number (for example `903.4`), so each chunk is a citable unit. |
| RAG-3/4 | Explanations cite card oracle text and rule numbers; rules questions not covered by the retrieved chunks return "not found". |
| MOD | One `llm` interface with Claude, Ollama, and replay implementations; timeouts, retries, token budgets, and Pydantic-validated structured output. |
| AGT | Native tool calling. Deck state is owned by code; tools are `search_pool`, `get_card`, `search_rules`, `analyze_deck`, and `propose_changes`. Turn and token caps, with partial results saved. |
| GRD-1 | A deterministic Commander validator: 100 cards, singleton except basics, color identity, Commander banned list, commander eligibility, and every card in the pool or a basic land. |
| GRD-2/5 | Proposals are queued. Applying a proposal or exporting a deck requires an approval record, and approvals, rejections, and executed actions go to an audit log. |
| GRD-3/4 | Card names, CSV fields, and oracle text are untrusted data: delimited in prompts and unable to trigger tools. The eval suite includes injection cases. |
| EVL | A retrieval set (recall@k, MRR, vector vs hybrid), a rules Q&A set (correctness, citation accuracy, "not found"), deck-build tasks (success rate, cost, latency, turns), and rubric-graded deck quality with a hand-checked sample. |
| OBS | A trace ID per request; every model and tool call is recorded with arguments, tokens, cost, latency, and outcome. |

## Architecture

```
 CLI ─┐
 MCP ─┼─> FastAPI ──> agent loop ──> llm (Claude | Ollama | replay)
      │               │   │
      │               │   └─ tools ──> retrieval (pgvector + Postgres full-text, fused with RRF)
      │               │                 guardrails (validator, proposals, approvals, audit)
      │               └─ deck state (owned by code)
      └─ ingestion (Scryfall bulk, Comprehensive Rules, pool parsers) ──> Postgres + pgvector
 observability: trace IDs, structured logs, model and tool call records
```

**Planned stack.** Each choice is confirmed in a decision record under `docs/decisions/` when its milestone starts.

- FastAPI, `httpx` with `tenacity` retries, and `pydantic-settings`.
- PostgreSQL 16 with pgvector, accessed through `psycopg` 3 with explicit SQL and no ORM, so every query is visible and explainable.
- The official `anthropic` SDK, and Ollama over its HTTP API.
- A local Ollama embedding model (for example `nomic-embed-text`), so embeddings need no API key in demo mode or CI.
- The official `mcp` Python SDK.
- `testcontainers` with a pgvector image for integration tests, and `structlog` for JSON logs.

**Card role tagging.** Composition analysis depends on knowing each card's role (ramp, card draw, removal, board wipe, and so on). The model classifies each card once, with schema-validated output, and the result is cached by the card's content hash, so re-ingestion never re-tags unchanged cards. Composition targets (for example about 36 lands, 10 ramp, 10 draw) produce soft warnings, not legality failures.

## Milestones

Each feature is built test-first, with one branch and pull request per milestone slice.

0. **Foundations:** settings, Docker Compose with pgvector, a hand-written Dockerfile, database migrations, the integration test harness, trace IDs and logging basics, and CI (lint, type check, unit and integration tests, secret and dependency scanning, image build).
1. **Ingestion:** Scryfall bulk download with caching, retries, and rate limiting; idempotent card upserts with content hashing and added, updated, unchanged, and removed counts; Comprehensive Rules fetch; text and CSV pool parsers. Name resolution handles double-faced cards and reports unknown names.
2. **Deck domain and validator:** deck state, the Commander validator, and commander eligibility. This is pure deterministic code and the most heavily unit-tested area.
3. **Model layer:** the provider interface; Claude, Ollama, and replay providers; budgets; structured output; call logging; the embeddings interface; card role tagging.
4. **Retrieval:** card embeddings with metadata filters, rules chunking by rule number, hybrid search, and a retrieval eval set comparing vector-only and hybrid search on recall@k and MRR.
5. **Agent and guardrails:** the bounded tool-calling loop, the draft-then-refine flow, the proposal queue, approvals, the audit log, cited explanations, and "not found" handling.
6. **Interfaces:** API endpoints (submit pool, draft, refine, approve or reject, export, ask), the CLI, and the MCP server.
7. **Evaluation and CI gate:** the rules Q&A set, the deck-build task set, rubric grading with a hand-check agreement rate, injection cases, and timestamped reports comparing variants. The CI regression eval combines deterministic retrieval and validator metrics with a small, budget-capped live model eval, and fails the build when results drop below thresholds.
8. **Demo readiness:** a seed script with a sample pool and commander, a demo mode that needs no API key, and a README with an architecture diagram, demo script, eval results, design decisions, and known limitations.
9. **Should and Could items:** a dashboard and alerts, cloud deployment with infrastructure as code, reranking, and a provider comparison report.

Milestones 2, 4, 5, and 7 are the core of the project. If time runs short, work is cut from milestone 9 and the optional requirements first.

## Known risk

Eval labels need Magic: The Gathering judgment: which cards are relevant to a query, which rule answers a question, and what makes a deck good. Labels come either from hand labelling or from checkable sources such as the rule text itself. That work is scheduled into milestones 4 and 7 rather than left to the end.
