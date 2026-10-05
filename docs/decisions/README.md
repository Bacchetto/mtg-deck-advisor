# Architecture Decision Records

A short record of every non-obvious choice in this project: what the situation was, what was decided, what else was considered, and what it costs.

"Why did you do it that way?" is easier to answer well when the reasoning was written down at the time rather than reconstructed months later.

## Format

One file per decision, named `NNNN-short-title.md`, numbered in the order they are recorded. Numbers are never reused and files are never deleted. A decision that gets reversed is superseded by a new ADR that says so, and the old one stays as a record of what was believed then.

Each ADR has four sections:

| Section | Answers |
|---|---|
| **Context** | What forced a decision? What constraints applied? |
| **Decision** | What was chosen, stated plainly. |
| **Alternatives** | What else was on the table, and why it lost. |
| **Consequences** | What this buys, and what it costs. Include the bad. |

Keep them short. A page is plenty.

Each ADR is linked from the module docstring of the code it governs, so someone reading that code finds the reasoning at the moment they need it.

## Index

| # | Decision | Applies to |
|---|---|---|
| [0001](0001-structured-logging-with-structlog.md) | Structured JSON logging with structlog, standard-library logs included | `observability` |
| [0002](0002-psycopg-with-explicit-sql.md) | psycopg 3 with explicit SQL, no ORM | `db`, all queries |
| [0003](0003-alembic-raw-sql-migrations-as-a-separate-step.md) | Alembic with raw SQL migrations, run as a separate step | `db.migrate`, migrations |
| [0004](0004-local-tooling-on-a-network-drive.md) | Local tooling that works from a network drive: build, don't mount | Compose, `Dockerfile`, local development |
| [0005](0005-http-retries-rate-limits-and-download-cache.md) | HTTP retries, rate limiting and the download cache | `ingestion.downloads`, `ingestion.scryfall` |
| [0006](0006-content-hashes-and-soft-deletes-for-ingestion.md) | Content hashes over normalised fields, and soft deletes, for ingestion | `ingestion`, `cards` table |
| [0007](0007-a-pure-commander-validator-that-cites-rules.md) | A pure Commander validator that reports every violation and cites its rule | `guardrails.commander`, `deck` |
| [0008](0008-one-model-interface-owned-by-the-project.md) | One model interface, owned by the project | `llm` |
| [0009](0009-local-models-chosen-by-measurement.md) | Local models, run natively and chosen by measurement | `llm.ollama`, Ollama settings |
| [0010](0010-chunking-rules-by-rule-number.md) | One chunk per rule number, embedded as the rule's own text (context measured as a loss) | `retrieval.text`, `retrieval.embeddings` |
| [0011](0011-retrieval-improvements-chosen-on-a-dev-set.md) | Retrieval improvements chosen on a dev set by a fixed rule, checked once on a held-out set | `retrieval`, embedding and reranking settings |
| [0012](0012-native-tool-calling-with-provider-turns-kept-verbatim.md) | Native tool calling, with each provider turn kept verbatim | `llm` |
| [0013](0013-an-agent-that-proposes-and-code-that-decides.md) | An agent that proposes, and code that decides: bounded loop, proposals, user approvals, delimited data | `agent`, `guardrails` |
| [0014](0014-interfaces-background-runs-a-thin-cli-and-mcp.md) | Interfaces: background runs with polling, a CLI that only calls the API, MCP with the client as the agent | `api`, `cli`, `mcp_server` |
