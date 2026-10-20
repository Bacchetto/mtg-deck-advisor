# The CLI and MCP paths

There are two ways to build a deck, and they differ in **whose model does the reasoning**:
- **The CLI** (`mtg-advisor`) runs this project's agent: Claude Sonnet 5.5, in the project's own
  loop, through the HTTP API.
- **MCP** (Claude Code, Claude Desktop) makes your client's model the agent. It calls the agent's
  tools directly, and this project's agent isn't used.

Both end at the same place. Every proposal goes through the same tool code and the same checks,
and waits as `pending` until you decide. The HTTP API is the CLI's path: the CLI is a thin client
of it.

## Side by side

| | CLI | MCP |
|---|---|---|
| **Who reasons** | This project's agent (Sonnet 5.5) | Your client's model |
| **Who pays for the model** | The project's API key | Whoever runs the client |
| **Limits** | 30 turns, and a cost cap checked before each model call | The client's own: the server sets none, because it makes no model calls |
| **How it reaches the system** | HTTP requests to the API | An MCP server over stdio, with its own database connection |
| **Runs** | Drafts and refines run in the background on the API server, and the CLI polls them | Each tool call runs when it's made. Each deck gets one run record per session, with the model recorded as `mcp:<client>` |
| **Tools and checks** | The agent's tools, through `agent.tools.execute` | The same tools through the same function, so the same validation, Commander rules, citation checks and `<untrusted>` delimiting |
| **Rules questions** | `ask`: the answer is shown only if every rule it cites was retrieved in the run, or it's "not found" | `search_rules` is available, but the client writes its own answer, with no check on what it cites |
| **Your decisions** | You type them, or run `approve`, `reject` and `approve-export` | Decision tools that bring up the client's Allow/Deny prompt every time, and only in clients known to show it |
| **Replay** | The demo replays it with no API key | No: the client's model is always live |
| **What it needs** | The API running (Docker Compose), with an Anthropic key or replay | The database and Ollama, and the server started by the client from the repository's `.venv` |

## Decisions over MCP

Approving, rejecting and exporting are the user's decisions. Over MCP, a model calling "approve"
isn't consent, so ([ADR 0015](decisions/0015-decisions-through-mcp-permission-prompts.md)):
- **Each decision tool is marked as requiring your interaction.** Claude Code then shows its
  Allow/Deny prompt every time, whatever your permission settings or mode, and denies it where it
  can't ask.
- **The prompt shows the server's own summary** of the decision, such as *"Approve: Bard, King of
  Dale as commander, 100 cards, for deck 'MCP test'"*. The model must pass that text word for
  word, so it can't slant what you're allowing.
- **Only clients known to honour the marking may decide:** `MCP_DECISION_CLIENTS`, by default
  Claude Code only. Others, Claude Desktop included, are sent to the CLI.
- **Each decision's audit entry says where it was made,** with `via: mcp` and the client's name.

Apply and export still refuse without a recorded approval, whichever path the approval came from.

## Which to use

- **The CLI** for the project's agent as built and measured: bounded runs, rules answers checked
  by code, and the demo with no key.
- **MCP** to build a deck in conversation, from a Claude Code session, with your client's model
  doing the work and you allowing each decision in its prompt.

Setup for each is in [setup.md](setup.md#6-using-it-the-cli-and-the-api) (the CLI) and
[setup.md](setup.md#7-from-claude-code-or-claude-desktop-mcp) (MCP). The reasons for the design
are in [ADR 0014](decisions/0014-interfaces-background-runs-a-thin-cli-and-mcp.md).
