# 0014 - Interfaces: background runs, a thin CLI, and MCP with the client as the agent

**Status:** Accepted, 2026-10-05. Amended by [ADR 0015](0015-approval-through-mcp-elicitation.md): the MCP server can now record decisions the user confirms in a form.
**Applies to:** `mtg_deck_advisor.api`, `mtg_deck_advisor.cli`, `mtg_deck_advisor.mcp_server`

## Context

Milestone 6 puts the agent behind three interfaces: an HTTP API with OpenAPI docs (ENG-5), a thin CLI for the demo, and an MCP server exposing the agent's tools (AGT-5). All three have to keep the guarantees of ADR 0013: the model only proposes, code checks, the user approves, and every step is recorded.

Two facts shaped the choices:

- **Agent runs are slow.** A draft takes about a minute and a refine about half that (Milestone 5's live runs). A rules answer takes seconds.
- **Through MCP, the reasoning model belongs to the client.** It is Claude Code or Claude Desktop, billed to whoever runs it, not this project's agent.

The owner chose (2026-10-04):
- long runs that start and are polled
- the CLI as a client of the API
- MCP exposing the agent's own tools
- verification from Claude Code

## Decision

**The API starts long runs and lets clients poll them.**
- **The request does the quick part:** `POST /pools/{id}/drafts` and `POST /decks/{id}/refinements` record the run, commit, and answer `202` with `Location: /runs/{id}`.
- **The agent runs afterwards,** in a background task on its own connection, under the same trace ID as the request (OBS-2).
- **Visible progress:** the loop saves turns and cost after every turn, so `GET /runs/{id}` shows the run advancing.
- **Interrupted runs:** when the server starts, a run still marked `running` can only have been cut off by a stop, so it is marked as an interrupted error. Its committed tool calls and proposals are kept.
- **Rules answers** take seconds and are answered directly.
- **Supporting code:** each flow is split into a quick `prepare_*` and a slow `execute_run`. A services factory on `app.state` builds each run's model client and cost cap, which is also how tests inject a scripted model.

**The user's steps map one-to-one onto the guardrail functions.**
- Approve, reject, apply, approve the export, and export each call `guardrails.approvals`.
- **Status codes:** a missing approval is `403`; a step the proposal can't take is `409`.
- **Refusals stay recorded:** a refusal's audit entry is committed before the error response, so it outlives the failed request.

**The CLI is a client of the API, and nothing more.**
- `mtg-advisor` turns each command into API calls (`API_URL`) and formats the answers. `draft` and `refine` poll the run and show progress.
- There is one code path for the work and the checking. The demo exercises the real API, and the CLI can't do anything the API wouldn't.

**MCP exposes the agent's tools, and the client's model is the agent.**
- **The tools:** the six agent tools, each with a `deck_id` scope, plus `list_pools`, `list_decks` and `new_deck`.
- **Low-level SDK:** the server uses the MCP SDK's low-level `Server`, so each tool's schema is exactly the agent's Pydantic schema (through `strict_schema`, limits described).
- **The same guardrails:** every call goes through `agent.tools.execute`, which brings the agent's validation, error results, `<untrusted>` delimiting, citation checks and recording. Each deck gets one `agent_runs` row per session, recorded as `mcp:<client>`.
- **No approve, apply or export tool.** A proposal made from Claude Code waits for the user in the CLI or the API, like any other.
- **Stdio:** the server runs over stdio, and logs go to stderr because stdout carries the protocol.

**No authentication yet.** The API listens only on `127.0.0.1`. Users, scoped keys, rate limits and a spending cap (SEC-3 to SEC-5) come before any deployment, in Milestone 9.

## Alternatives

**Hold the request open until the run ends.** It's simpler, but it needs long client and proxy timeouts, and a dropped connection loses the answer. The run itself would survive, because it's recorded. Polling is the usual shape for jobs that take minutes, and the run record already existed to poll.

**A CLI that calls the service functions directly** (what `scripts/agent_run.py` did). It works without a server, but then there are two paths to keep in step, and the demo wouldn't exercise the API.

**MCP tools that run this project's agent** (`draft_deck`, `ask_rules`). Every call would spend this project's API budget on behalf of whichever client asked, and the client's own model would sit idle. Exposing the tools lets the client's model do the reasoning, under the same guardrails, at no cost to the project. A paid "run our agent" tool could be added later, behind the authentication and spend caps of Milestone 9.

**`MCPServer` (the SDK's high-level server).** It derives each schema from a function's signature, which would mean restating every agent tool's arguments. The low-level server takes the schemas as they are.

## Consequences

- **One set of guardrails covers every way in.** Proposals from the built-in agent, the API, the CLI or Claude Code all end in the same `pending` state, and they all go through the same approval functions and audit log.
- **The demo runs with no key:** the API in replay mode serves the recorded runs through the CLI exactly.
- **Background tasks live in the API process.** A restart interrupts runs in flight. They are recorded as interrupted rather than lost, but they aren't resumed. A job queue would be the next step if runs had to survive restarts.
- **Through MCP, the citation check uses what the session's tools returned,** as it does for the agent. A client that cites from its own knowledge gets its proposal rejected.
- **Until Milestone 9, anyone who can reach port 8000 can do anything,** spending included. That's acceptable only because it's bound to localhost.
