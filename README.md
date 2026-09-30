# MTG Deck Advisor

An AI-assisted Magic: The Gathering deck advisor, built as a project to demonstrate
production AI engineering: RAG with citations, a bounded tool-calling agent, deterministic
guardrails with human approval, and an evaluation suite that gates CI.

> Status: early scaffolding.

The first version builds a legal Commander deck from the cards a user owns: submit a card
pool and a commander, get a cited draft, then refine it by approving or rejecting the
agent's proposed changes. See [docs/project-plan.md](docs/project-plan.md) for the product,
architecture, and milestones.

## Goals

The full requirements, with IDs and acceptance criteria, are in
[docs/requirements.md](docs/requirements.md). The core of the project:

- **Retrieval (RAG):** semantic search over data combined with exact metadata filters,
  chunked long-form documents, answers that cite their source, and an explicit "not found"
  instead of an invented answer.
- **Agent:** a loop that uses native tool calling, capped by a turn limit and a token budget.
  Code owns application state, and invalid tool calls are returned to the model as errors.
- **Guardrails:** the model proposes and deterministic code decides. Anything with side effects
  needs explicit user approval, and retrieved content is treated as untrusted.
- **Evaluation:** labelled retrieval and answer test sets (recall@k, MRR, citation accuracy),
  model-graded evals checked against hand grading, and a regression eval that fails CI.

Supporting work:

- **Ingestion:** cached, retry-aware loading from an external API and from files. Re-running
  it is idempotent, using content hashing.
- **Model integration:** one internal interface for all model calls, schema-validated outputs,
  and providers switchable by config (hosted and local).
- **Observability:** every model and tool call is logged with tokens, cost, and latency, and
  linked by trace ID.
- **Engineering:** strict typing, test-first development, integration tests against Postgres
  with pgvector, Docker Compose, CI with security scans, and a demo mode that needs no API key.

## Layout

```
src/mtg_deck_advisor/
  ingestion/      external API + file loaders, caching, idempotent upserts  (ING)
  retrieval/      chunking, embeddings, hybrid search, cited answers        (RAG)
  llm/            single internal interface for all model calls             (MOD)
  agent/          tool-calling agent loop and tools                         (AGT)
  guardrails/     validation, approvals, audit log                          (GRD)
  observability/  logging, tracing, cost/latency metrics                    (OBS)
  api/            HTTP API                                                  (ENG-5)
  mcp_server/     MCP server exposing agent tools                           (AGT-5)
tests/unit/         fast tests, model calls mocked
tests/integration/  tests against real services (Postgres + pgvector)
evals/              labelled datasets and eval reports
scripts/            seed and utility scripts
docs/               design notes
```

## Development

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install          # secret scanning + ruff on every commit
cp .env.example .env

docker compose up -d --wait              # Postgres 16 + pgvector on 127.0.0.1:5432
python -m mtg_deck_advisor.db.migrate    # apply database migrations
python -m mtg_deck_advisor.api           # API on http://127.0.0.1:8000 (docs at /docs)

ruff check . && ruff format --check .
mypy
pytest -m "not integration"   # unit tests: fast, no Docker
pytest -m integration         # integration tests: start their own pgvector container
```

Integration tests need a running Docker engine (on Windows, Rancher Desktop with the
dockerd engine works). They start and remove their own database container, independent
of the Compose one.
