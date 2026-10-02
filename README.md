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

## Running it

The whole stack (database, migrations, API) with Docker Compose:

```bash
cp .env.example .env
docker compose up -d --build --wait
```

Then, while it's running:

- http://127.0.0.1:8000/health: `{"status": "ok", "database": "ok"}`
- http://127.0.0.1:8000/docs: interactive API documentation (Swagger UI), generated
  from the code and served by the running API

To load the data: card data from Scryfall (about 35,000 cards, from a 25 MB download) and
the Comprehensive Rules (about 3,200 rules). Downloads are cached, so later runs reuse them:

```bash
docker compose run --rm ingest all     # or `cards`, or `rules`
```

Ingestion is idempotent: run it again and nothing changes unless Scryfall's data did.

```text
cards: 34664 added, 0 updated, 0 unchanged, 0 removed      # first run
rules: 3166 added, 0 updated, 0 unchanged, 0 removed

cards: 0 added, 0 updated, 34664 unchanged, 0 removed      # second run
rules: 0 added, 0 updated, 3166 unchanged, 0 removed
```

Then embed the cards and rules for semantic search. This uses the local embedding model through Ollama
(see [Local models with Ollama](#local-models-with-ollama)), so it's free. The first run takes
about 8 minutes on the development machine (RX 9070 XT), and later runs only embed cards and rules whose
text has changed:

```bash
docker compose run --rm embed all       # or `cards`, or `rules`
```

```text
cards: 32,116 added, 0 updated, 0 unchanged; 449.6 s     # first run
rules: 3,166 added, 0 updated, 0 unchanged; 42.9 s

cards: 0 added, 0 updated, 32,116 unchanged; 0.9 s        # second run
rules: 0 added, 0 updated, 3,166 unchanged; 0.1 s
```

`docker compose down` stops everything. `docker compose down -v` also deletes the data.
The application image is 273 MB.

## Models

Model calls go through one interface ([ADR 0008](docs/decisions/0008-one-model-interface-owned-by-the-project.md)),
and the provider is configuration (`MODEL_PROVIDER` in `.env`):

- **`anthropic`** (default): Claude. Needs `ANTHROPIC_API_KEY` in `.env`. Every call is
  costed and logged, and a run stops before it could exceed `RUN_COST_CAP_USD`.
- **`ollama`**: a local model, free. Also used for embeddings.

### Local models with Ollama

Ollama runs natively (not in Docker), so it can use the GPU
([ADR 0009](docs/decisions/0009-local-models-chosen-by-measurement.md)). Install
[Ollama](https://ollama.com), then pull the models this project uses:

```bash
ollama pull qwen3-embedding:0.6b   # embeddings (0.6 GB)
ollama pull qwen3:14b              # local chat model (9.3 GB)
```

The app reaches Ollama at `http://127.0.0.1:11434`; containers use
`http://host.docker.internal:11434` (set in Compose).

### Card roles

Each card's roles (ramp, removal, card draw...) are tagged by a model, on demand: only
cards in pools that are actually submitted, and only once. Tags are stored and reused
until a card's text or the role prompt changes. `ROLE_TAGGING_MODEL` picks the model
(Claude Opus 5.5, chosen by measurement:
[report](evals/reports/2026-10-02-role-tagging-comparison.md)). To tag a pool from the
command line:

```bash
python -m mtg_deck_advisor.llm.tag_roles --pool my-pool.txt   # or a .csv export
```

A new 300-card pool costs about $0.37 and takes about 35 seconds. Tagging it again costs
nothing, because the tags are already stored. The run's projected cost is checked against
`RUN_COST_CAP_USD` before any call is made.

## Development

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install          # secret scanning + ruff on every commit
cp .env.example .env

docker compose up -d --wait db           # just the database: Postgres 16 + pgvector
python -m mtg_deck_advisor.db.migrate    # apply database migrations
python -m mtg_deck_advisor.api           # API on http://127.0.0.1:8000
python -m mtg_deck_advisor.ingestion all     # load cards (Scryfall) and the rules

ruff check . && ruff format --check .
mypy
pytest -m "not integration"   # unit tests: fast, no Docker
pytest -m integration         # integration tests: start their own pgvector container
```

The API container and a host-run API both use port 8000, so run one or the other. Code
is built into the image rather than mounted (see
[ADR 0004](docs/decisions/0004-local-tooling-on-a-network-drive.md)), so after a change
to the source, `docker compose up -d --build` picks it up.

Integration tests need a running Docker engine (on Windows, Rancher Desktop with the
dockerd engine works). They start and remove their own database container, independent
of the Compose one.
