# MTG Deck Advisor

An AI-assisted Magic: The Gathering deck advisor, built as a project to demonstrate
production AI engineering: RAG with citations, a bounded tool-calling agent, deterministic
guardrails with human approval, and an evaluation suite that gates CI.

It builds a legal Commander deck from the cards you own. Submit your card pool (a list, or a
CSV export such as TCGplayer's) and say what to build around. The agent drafts a deck and cites
its cards and rules. You then approve, reject or refine its proposals, and export the decklist.
You can drive it from the `mtg-advisor` CLI, the HTTP API, or an MCP client such as Claude Code.
See [docs/project-plan.md](docs/project-plan.md) for the product, architecture, and milestones.

## Status

**Working end to end.** Milestones 0 to 6 are done: ingestion, the deck validator, the model
layer, retrieval, the agent with its guardrails, and the API, CLI and MCP interfaces.
**Milestone 7, evaluation, is in progress.** A free eval gate already runs on every pull request.
The rules Q&A and deck-build eval sets, injection cases and a capped live eval are next. After
that come demo readiness (Milestone 8) and the optional items (Milestone 9). Progress is tracked
in the [milestone issues](https://github.com/Bacchetto/mtg-deck-advisor/issues?q=label%3Amilestone).

## What's built

The full requirements, with IDs and acceptance criteria, are in
[docs/requirements.md](docs/requirements.md). Each choice is recorded in a decision record
under [docs/decisions/](docs/decisions/).

- **Retrieval (RAG):** hybrid vector and keyword search over about 32,000 cards and 3,166
  rules, with exact filters, and card searches reranked by a local model. It was tuned on a dev
  set and checked once on a held-out set: card recall@10 went from 0.63 to 0.72 and MRR@10
  from 0.54 to 0.79 ([Search](#search)).
- **Agent:** native tool calling on Claude, capped by a turn limit and a cost cap checked
  before each call. Every run is recorded with its transcript, tool calls and cost. A live
  draft of a legal 100-card deck took 5 turns and cost $0.12 ([The agent](#the-agent)).
- **Guardrails:** the model proposes and deterministic code decides. Proposals are checked
  against the Commander rules, your pool and the run's own lookups. Applying and exporting
  need your approval, every step goes into an append-only audit log, and card text reaches
  the model marked as untrusted.
- **Interfaces:** an HTTP API with runs in the background, the `mtg-advisor` CLI, and an MCP
  server whose decision tools always ask you first ([Using it](#using-it-the-api-and-the-mtg-advisor-cli)).
- **Evaluation:** labelled retrieval sets (dev and held-out), with reports. A CI gate scores
  retrieval, the validator and pool resolution on a committed embedding snapshot, and fails
  the build below its thresholds.
- **Ingestion:** cached, retry-aware loading from Scryfall and the Comprehensive Rules.
  Re-running it is idempotent, using content hashing.
- **Model integration:** one internal interface for all model calls, with providers switchable
  by config: Claude, local models through Ollama, and a replay provider for a demo with no API key.
- **Observability:** every model and tool call is logged with tokens, cost and latency, linked
  by trace ID.
- **Engineering:** strict typing (mypy), test-first development, integration tests against
  Postgres with pgvector, Docker Compose, and CI with security scans and an image build.

## Layout

```
src/mtg_deck_advisor/
  ingestion/      external API + file loaders, caching, idempotent upserts  (ING)
  deck/           deck state, card facts, versioned deck storage
  retrieval/      chunking, embeddings, hybrid search, reranking            (RAG)
  llm/            single internal interface for all model calls             (MOD)
  agent/          tool-calling agent loop, tools and flows                  (AGT)
  guardrails/     deck validator, proposal checks, approvals, audit log     (GRD)
  evaluation/     embedding snapshot and the CI eval gate                   (EVL)
  observability/  logging, tracing, cost/latency metrics                    (OBS)
  db/             migrations and connections
  api/            HTTP API                                                  (ENG-5)
  cli.py          the mtg-advisor CLI, a thin client of the API
  mcp_server/     MCP server exposing agent tools                           (AGT-5)
tests/unit/         fast tests, model calls mocked
tests/integration/  tests against real services (Postgres + pgvector)
evals/              labelled datasets, embedding snapshot, thresholds, reports
scripts/            eval, experiment and utility scripts
docs/               project plan, requirements, decision records
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
about 24 minutes on the development machine (RX 9070 XT), and later runs only embed cards and rules whose
text has changed:

```bash
docker compose run --rm embed all       # or `cards`, `rules`, or `summaries`
```

```text
cards: 32,116 added, 0 updated, 0 unchanged; 1327.6 s    # first run
rules: 3,166 added, 0 updated, 0 unchanged; 121.9 s

cards: 0 added, 0 updated, 32,116 unchanged; 0.9 s        # second run
rules: 0 added, 0 updated, 3,166 unchanged; 0.1 s
```

`all` also covers **card summaries**. These are one plain-English sentence per card ("Destroys all
creatures... used for board wipe"), written by the local chat model (`SUMMARY_MODEL`, `qwen3:14b`)
and embedded for searches within a pool. On an empty database they take about 6.6 hours of GPU time
for the whole catalogue. After that, only new or changed cards are summarised, at about 0.75 s each.
If the retrieval experiments' summaries are already in the database, copy them instead:
`python scripts/import_experiment_summaries.py`.

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
ollama pull qwen3-embedding:8b    # embeddings (4.7 GB), truncated to 1,024 dimensions
ollama pull qwen3:14b             # local chat model and card summaries (9.3 GB)
ollama pull qwen3:8b              # reranks card searches (5.2 GB)
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

## Search

Cards and rules are searchable by meaning (vector search with local embeddings), by
keyword (Postgres full-text search), or both, fused by reciprocal rank (hybrid). Card
searches take exact filters: color identity within the commander's, card types, a mana
value range, and "in this pool". Each rule is one chunk, cited by its number
([ADR 0010](docs/decisions/0010-chunking-rules-by-rule-number.md)).

Card search runs in two stages:
1. **First stage, hybrid search:** vector search over 8b embeddings, keyword search at half weight, and, for searches within a pool, each card's one-sentence summary.
2. **Second stage, reranking:** a local model (`qwen3:8b`) reorders the top 20, reading each card's rules text.

Rules search is vector search. Everything runs locally and is free.

Every choice was made by measurement: on a dev set of 44 card queries and 45 rules questions, under a
rule fixed in advance ([ADR 0011](docs/decisions/0011-retrieval-improvements-chosen-on-a-dev-set.md)).
The result was then checked once on a **held-out** set of 41 card queries and 22 rules questions,
written before any tuning:

| Held-out set ([report](evals/reports/2026-10-04-retrieval-test.md)) | Before | Now |
|---|---|---|
| Cards: recall@10 / MRR@10 | 0.63 / 0.54 | **0.72 / 0.79** |
| Rules: recall@10 / MRR@10 | 1.00 / 0.67 | **1.00 / 0.75** |
| Median card search | 24 ms | 1.1 s |

- **What MRR means here:** 0.79 means the right card is usually first.
- **The biggest gain is for deck-building searches within a pool:** recall@10 0.56 → 0.76.
- **Known weakness:** a bare mechanic name ("recover") is now found less reliably than before (0.80 → 0.30 recall@10, on three queries).
- **GPU memory:** search needs about 12 GB of VRAM for the embedder and the reranker. Set `RERANK_MODEL=` to turn reranking off on a smaller GPU.

Rerun the eval with `python scripts/evaluate_retrieval.py` (dev set, local and free). The experiments
that chose each setting are in `scripts/retrieval_experiments.py` and
[evals/reports/retrieval-experiments/](evals/reports/retrieval-experiments/).

**The eval gate in CI.** Every pull request runs `python -m mtg_deck_advisor.evaluation.gate`. It's free
and needs no local model: it loads a committed embedding snapshot (`evals/snapshot/`: about 4,100 cards,
every rule, and the eval queries) into a throwaway Postgres. It then scores retrieval, the deck validator
on labelled decks, and pool resolution, and fails the build below any threshold in `evals/thresholds.toml`
([ADR 0016](docs/decisions/0016-evaluation-a-free-snapshot-gate-and-a-capped-live-eval.md)). After
changing a labelled query or what gets embedded, export the snapshot again with
`python -m mtg_deck_advisor.evaluation.snapshot` (about 40 s; Ollama must be running).

## The agent

The agent drafts a Commander deck from your card pool, refines it on request, and answers
rules questions. **It only proposes. Code checks every proposal, and nothing changes without your
approval** ([ADR 0013](docs/decisions/0013-an-agent-that-proposes-and-code-that-decides.md)).

- **How it runs:** Claude Sonnet 5.5 (`AGENT_MODEL`), with native tool calling
  ([ADR 0012](docs/decisions/0012-native-tool-calling-with-provider-turns-kept-verbatim.md)).
- **Its six tools:**
  - **Read-only:** search the pool, look up a card, search the rules, analyze the deck.
  - **Propose:** a whole deck, or a set of changes.
- **Bounded:** at most `AGENT_MAX_TURNS` model calls, and a cost cap (`AGENT_COST_CAP_USD`)
  checked before each call. Every run is recorded with its transcript, tool calls and cost,
  whether it finishes or not.
- **Checked by code:** each proposal is checked against the Commander rules and your pool. Its
  citations must be rules and cards the run actually looked up. A failing proposal goes back
  to the agent with every problem listed.
- **Approved by you:** approving, applying and exporting are not tools, and apply and export
  refuse to act without your approval record. Every step is in an append-only audit log.
- **Grounded rules answers:** an answer is shown only if every rule it cites was retrieved in
  the run. Otherwise the answer is "not found".
- **Card text is treated as data:** card and rule text reach the model inside `<untrusted>`
  delimiters. Even an obeyed injection has nothing to call that changes anything.

First live runs ([report](evals/reports/2026-10-04-agent-live-runs.md)), on a 300-card pool:

| Run | Turns | Cost | Time |
|---|---|---|---|
| Draft (ended in a legal 100-card deck) | 5 | $0.12 | 51 s |
| Refine (swap three cards) | 4 | $0.06 | 19 s |
| Rules question (four asked; one correctly "not found") | 2 each | under $0.01 each | 4–8 s |

### Using it: the API and the `mtg-advisor` CLI

Everything goes through the HTTP API (documented at http://127.0.0.1:8000/docs). The
`mtg-advisor` command is a thin client of it, installed with the package (`pip install -e .`),
and finds the API at `API_URL`. Drafts and refines run on the server and take a minute or so;
the CLI waits and shows progress (`--no-wait` to return at once, `mtg-advisor run RUN` to check later).

**The whole build in one command:** `mtg-advisor build pool.txt` submits the pool (or takes a pool ID,
or lets you choose from your pools), asks what to build around (a commander or a theme; `?` lists
the commanders your pool can use), drafts, shows the proposal, and then
waits for you: `[a]pprove` (approve and save), `[r]eject` (with a reason, which is passed on to a
new attempt), `[c]hange "..."` (a refine), `[e]xport` to a file, or `[q]uit`. A rejected first draft
is drafted again into the same deck. `mtg-advisor build --deck DECK` picks a deck up where you left it.
Nothing is approved or exported unless you type it.

Pools can be a pasted list (`1 Sol Ring`, with or without set codes) or a CSV export with a name
column, including the TCGplayer app's collection export: names come from "Product Name", counts
from "Total Quantity" or "Add to Quantity", other games' rows are skipped, and variant tags such
as "(Showcase)" are ignored when matching.

The same steps, one command each:

```bash
mtg-advisor pool add evals/datasets/pool_300.txt    # submit a pool (a list, or --csv)
mtg-advisor draft POOL [--name NAME]                 # the agent drafts; prints its proposals
mtg-advisor proposal PROPOSAL                        # rationale, problems, decklist
mtg-advisor approve PROPOSAL                         # your decision (or: reject --reason ...)
mtg-advisor apply PROPOSAL                           # saved as version 1
mtg-advisor refine DECK --request "Add removal"      # proposes changes to the saved deck
mtg-advisor approve-export DECK && mtg-advisor export DECK > deck.txt
mtg-advisor rename DECK "Rat pack"                  # only the name changes
mtg-advisor decks [--archived]                      # your decks; archive DECK hides one
mtg-advisor ask "How much commander damage loses the game?"
```

Applying or exporting without your approval is refused (`403`), and the attempt is audited.
A deck you don't name is called "New <pool> deck" until its first version is applied, then
takes its commander's name; default names get " (2)", " (3)" rather than repeat another deck's.
Archiving hides a deck from lists and keeps all of its history; it can't change or be exported
until `mtg-advisor unarchive DECK`. `--json` prints the API's answers.

**No API key needed for a demo:** with `MODEL_PROVIDER=replay`, the recorded runs replay through the
API and the CLI at no cost. Add `evals/datasets/pool_300.txt`, draft with no request, and refine
with the request in [the run report](evals/reports/2026-10-04-agent-live-runs.md) to see the whole
flow exactly as it ran live.

### From Claude Code or Claude Desktop (MCP)

The same tools the agent uses are available to an MCP client, whose own model then does the
deck building, under the same checks
([ADR 0014](docs/decisions/0014-interfaces-background-runs-a-thin-cli-and-mcp.md)). The client
gets `list_pools`, `list_decks`, `new_deck`, `rename_deck`, `archive_deck`, `unarchive_deck`, the agent's six tools scoped by deck, and your own
steps: `list_proposals`, `show_proposal`, `apply_proposal`, `export_deck`, and the decisions
`approve_proposal`, `reject_proposal` and `approve_export`. So a deck can be built from draft to
export without leaving the client.

**Decisions stay yours.** The decision tools are marked as requiring your interaction, so Claude
Code always asks you to **Allow** them, whatever your permission settings or mode, and the prompt
shows the server's own summary of the decision, such as *"Approve: Bard, King of Dale as commander,
100 cards, for deck 'MCP test'"*. Deny, and nothing changes. Only clients known to ask first may
decide (`MCP_DECISION_CLIENTS`, by default Claude Code); others are sent to `mtg-advisor`
([ADR 0015](docs/decisions/0015-decisions-through-mcp-permission-prompts.md)).

The server needs the database and Ollama running, like the API.

**Claude Code** picks it up from [`.mcp.json`](.mcp.json) in the repository; open a session in the
repository folder and approve `mtg-advisor` when asked. The command there is the Windows venv path
(`.venv/Scripts/python.exe`); on Linux or macOS change it to `.venv/bin/python`. The VS Code
extension may list it as pending with no way to approve it. If so, enable it in
`.claude/settings.local.json` (git-ignored) and start a new session:

```json
{ "enabledMcpjsonServers": ["mtg-advisor"] }
```

**Claude Desktop:** add the same command to `claude_desktop_config.json` under `mcpServers`, with the
full path to the venv's Python. Desktop doesn't start it from the repository, so it won't read `.env`:
put the settings the server needs (at least `DATABASE_URL`, and `OLLAMA_BASE_URL` if not the
default) in the server's `env` block.

Then ask, for example: "List my card pools, start a deck from the one called Demo pool, and propose
a legal Commander deck." Then "approve it", "save it" and "export it": each decision brings up
Claude Code's permission prompt for you to allow.

**The API has no authentication yet.** It listens only on `127.0.0.1`. Users, scoped keys, rate limits
and a spending cap (SEC-3 to SEC-5) come before any deployment, in Milestone 9.

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
