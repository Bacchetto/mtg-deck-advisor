# Full setup: your own cards, live models

The [demo](../README.md#try-the-demo) replays a recorded session and needs only Docker. This page
covers the full setup:
- every Scryfall card and the Comprehensive Rules
- local models for search
- Claude for the agent
- your own card collection

It also covers driving it from the CLI, the API or an MCP client.

**What it needs:**
- **Docker** (on Windows, Rancher Desktop with the dockerd engine works).
- **[Ollama](https://ollama.com)** with about 19 GB of models, ideally on a GPU with 12 GB of VRAM or more.
- **An Anthropic API key**, for the agent and for role tagging. Every run has a cost cap.

## 1. Start the stack

```bash
cp .env.example .env      # then set ANTHROPIC_API_KEY in .env
docker compose up -d --build --wait
```

This starts the database, runs migrations, and starts the API. The seed step skips a database that
already has cards, and on an empty one it loads the demo dataset. To start from an empty database
instead, run `docker compose down -v` first.

While it's running:
- http://127.0.0.1:8000/health gives `{"status": "ok", "database": "ok"}`.
- http://127.0.0.1:8000/docs is the interactive API documentation.

`docker compose down` stops everything, and `docker compose down -v` also deletes the data.

## 2. Load the cards and rules

Card data comes from Scryfall: about 35,000 cards, from a 25 MB download. The Comprehensive Rules
are about 3,200 rules. Downloads are cached, so later runs reuse them:

```bash
docker compose run --rm ingest all     # or `cards`, or `rules`
```

Ingestion is idempotent: run it again, and nothing changes unless Scryfall's data did.

```text
cards: 34664 added, 0 updated, 0 unchanged, 0 removed      # first run
rules: 3166 added, 0 updated, 0 unchanged, 0 removed

cards: 0 added, 0 updated, 34664 unchanged, 0 removed      # second run
rules: 0 added, 0 updated, 3166 unchanged, 0 removed
```

## 3. Local models with Ollama

Ollama runs natively, not in Docker, so it can use the GPU
([ADR 0009](decisions/0009-local-models-chosen-by-measurement.md)). Install
[Ollama](https://ollama.com), then pull the models:

```bash
ollama pull qwen3-embedding:8b    # embeddings (4.7 GB), truncated to 1,024 dimensions
ollama pull qwen3:14b             # card summaries, and the local chat model (9.3 GB)
ollama pull qwen3:8b              # reranks card searches (5.2 GB)
```

The app reaches Ollama at `http://127.0.0.1:11434`, and containers at
`http://host.docker.internal:11434` (set in Compose). Search needs about 12 GB of VRAM for the
embedder and the reranker. On a smaller GPU, set `RERANK_MODEL=` to turn reranking off.

## 4. Embed the cards and rules

Search needs embeddings, made by the local model, so this is free. The first run takes about 24
minutes on the development machine (RX 9070 XT). Later runs embed only cards and rules whose text
has changed:

```bash
docker compose run --rm embed all       # or `cards`, `rules`, or `summaries`
```

```text
cards: 32,116 added, 0 updated, 0 unchanged; 1327.6 s    # first run
rules: 3,166 added, 0 updated, 0 unchanged; 121.9 s

cards: 0 added, 0 updated, 32,116 unchanged; 0.9 s        # second run
rules: 0 added, 0 updated, 3,166 unchanged; 0.1 s
```

**`all` also writes card summaries:**
- **What they are:** one plain-English sentence per card ("Destroys all creatures... used for board wipe"), written by `SUMMARY_MODEL` (`qwen3:14b`). They're embedded for searches within a pool.
- **How long they take:** about 6.6 hours of GPU time for the whole catalogue on an empty database. After that, only new or changed cards are summarised, at about 0.75 s each.
- **A shortcut:** if the retrieval experiments' summaries are already in the database, copy them instead with `python scripts/import_experiment_summaries.py`.

## 5. Models and cost

All model calls go through one interface
([ADR 0008](decisions/0008-one-model-interface-owned-by-the-project.md)), and the provider is
configuration (`MODEL_PROVIDER` in `.env`):

- **`anthropic`** (the default): Claude. It needs `ANTHROPIC_API_KEY`. Every call is costed and
  logged, and a run stops before it could exceed its cap (`AGENT_COST_CAP_USD` for the agent,
  `RUN_COST_CAP_USD` otherwise).
- **`ollama`**: a local model, free.
- **`replay`**: recorded responses from `REPLAY_DIR`, free and offline. Embeddings and reranking
  replay too ([recordings/README.md](../recordings/README.md)).

**The agent** runs on Claude Sonnet 5.5 (`AGENT_MODEL`). A draft costs about $0.10-0.12, a refine
about $0.04-0.06, and a rules answer under $0.01.

**Card roles** (ramp, removal, card draw...) are tagged by Claude Opus 5.5 (`ROLE_TAGGING_MODEL`,
chosen by measurement: [report](../evals/reports/2026-10-02-role-tagging-comparison.md)).
- **When:** on demand, only for cards in pools that are actually submitted, and only once.
- **Reuse:** tags are stored, and reused until a card's text or the role prompt changes.
- **Cost:** a new 300-card pool costs about $0.37 and takes about 35 seconds. Tagging it again costs nothing.

To tag a pool in advance:

```bash
python -m mtg_deck_advisor.llm.tag_roles --pool my-pool.txt   # or a .csv export
```

## 6. Using it: the CLI and the API

Everything goes through the HTTP API. The `mtg-advisor` command is a thin client of it: it's
installed with the package (`pip install -e .`, or use it inside the app container with
`docker compose exec app mtg-advisor ...`), and finds the API at `API_URL`. Drafts and refines run
on the server and take a minute or so. The CLI waits and shows progress, or `--no-wait` returns at
once and `mtg-advisor run RUN` checks later.

**The whole build in one command:** `mtg-advisor build pool.txt`. It:
1. submits the pool (or takes a pool ID, or lets you choose from your pools)
2. asks what to build around: a commander or a theme, `?` to list the commanders your pool can use, or Enter to let the agent choose
3. drafts, and shows the proposal

Then it waits for you:
- **`[a]pprove`:** approve and save.
- **`[r]eject`:** with a reason, which is passed on to a new attempt. A rejected first draft is drafted again into the same deck.
- **`[c]hange`:** a refine.
- **`[e]xport`:** to a file.
- **`[q]uit`:** `mtg-advisor build --deck DECK` picks a deck up later where you left it.

Nothing is approved or exported unless you type it.

**Pools** can be a pasted list (`1 Sol Ring`, with or without set codes), or a CSV export with a
name column. That includes the TCGplayer app's collection export:
- Names come from "Product Name", and counts from "Total Quantity" or "Add to Quantity".
- Other games' rows are skipped.
- Variant tags such as "(Showcase)" are ignored when matching.
- For crossover printings such as "Lorien Brooch - Trailblazer's Boots", the real name after the dash is used.

The same steps, one command each:

```bash
mtg-advisor pool add my-cards.txt                   # submit a pool (a list, or --csv)
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

**Approval and auditing:** applying or exporting without your approval is refused (`403`), and the attempt is audited.

**Deck names:**
- A deck you don't name is called "New <pool> deck" until its first version is applied. Then it takes its commander's name.
- Default names get " (2)", " (3)" rather than repeat another deck's.

**Archiving** hides a deck from lists and keeps all of its history. An archived deck can't change or be exported until `mtg-advisor unarchive DECK`.

**`--json`** prints the API's answers.

## 7. From Claude Code or Claude Desktop (MCP)

The agent's tools are also available to an MCP client, whose own model then does the deck
building, under the same checks
([ADR 0014](decisions/0014-interfaces-background-runs-a-thin-cli-and-mcp.md)). The client gets:
- **Pools and decks:** `list_pools`, `list_decks`, `new_deck`, `rename_deck`, `archive_deck`, `unarchive_deck`.
- **The agent's six tools,** scoped by deck.
- **Your own steps:** `list_proposals`, `show_proposal`, `apply_proposal` and `export_deck`.
- **The decisions:** `approve_proposal`, `reject_proposal` and `approve_export`.

**Decisions stay yours:**
- **Claude Code always asks you to Allow them.** The decision tools are marked as requiring your interaction, so this holds whatever your permission settings or mode. The prompt shows the server's own summary of the decision, such as *"Approve: Bard, King of Dale as commander, 100 cards, for deck 'MCP test'"*.
- **Deny, and nothing changes.**
- **Only clients known to ask first may decide.** That's `MCP_DECISION_CLIENTS`, by default Claude Code. Others are sent to `mtg-advisor` ([ADR 0015](decisions/0015-decisions-through-mcp-permission-prompts.md)).

The server needs the database and Ollama running, like the API.

**Claude Code** picks it up from [`.mcp.json`](../.mcp.json) in the repository. Open a session in
the repository folder, and approve `mtg-advisor` when asked.
- **Its command is the Windows venv path** (`.venv/Scripts/python.exe`). On Linux or macOS, change it to `.venv/bin/python`.
- **The VS Code extension may list it as pending,** with no way to approve it. If so, enable it in `.claude/settings.local.json` (git-ignored) and start a new session:

```json
{ "enabledMcpjsonServers": ["mtg-advisor"] }
```

**Claude Desktop:** add the same command to `claude_desktop_config.json` under `mcpServers`, with
the full path to the venv's Python. Desktop doesn't start it from the repository, so it won't read
`.env`. Put the settings the server needs in the server's `env` block: at least `DATABASE_URL`,
and `OLLAMA_BASE_URL` if it's not the default.

Then ask, for example, "List my card pools, start a deck from the one called Demo collection, and
propose a legal Commander deck." Then say "approve it", "save it" and "export it". Each decision
brings up the client's permission prompt for you to allow.

**The API has no authentication yet.** It listens only on `127.0.0.1`. Users, scoped keys, rate
limits and a spending cap come before any deployment.

## 8. The evals

- **The free gate:** `python -m mtg_deck_advisor.evaluation.gate`. It's what every pull request runs, from a committed embedding snapshot.
- **The paid suites:** `python -m mtg_deck_advisor.evaluation.rules_qa`, `deck_tasks` and `injection`. They need a key and Ollama, and each asks for a budget.
- **Hand grading:** `python -m mtg_deck_advisor.evaluation.hand_grade`.
- **After changing a labelled query, or what gets embedded:** export the snapshot again with `python -m mtg_deck_advisor.evaluation.snapshot` (about 40 s, with Ollama running).
- **After changing the demo collection:** export the demo dataset again with `python -m mtg_deck_advisor.demo.dataset`.
- **The design:** see [ADR 0016](decisions/0016-evaluation-a-free-snapshot-gate-and-a-capped-live-eval.md) and the [Milestone 7 report](../evals/reports/2026-10-08-milestone-7.md).
