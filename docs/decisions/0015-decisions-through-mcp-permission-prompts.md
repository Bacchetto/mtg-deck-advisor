# 0015 - Decisions through MCP, allowed in the client's own permission prompt

**Status:** Accepted, 2026-10-05
**Applies to:** `mtg_deck_advisor.mcp_server`
**Amends:** [ADR 0014](0014-interfaces-background-runs-a-thin-cli-and-mcp.md), which gave the MCP server no way to decide

## Context

ADR 0014 exposed only the agent's tools over MCP, so a deck drafted in Claude Code had to be approved, applied and exported in the CLI or the API. The owner asked for a deck to be buildable end to end from one place: a Claude Code session in VS Code.

The obstacle is GRD-2: a side effect needs **the user's** approval. If calling a tool were enough, the model could approve its own proposal, or be talked into it by injected card text. "The model called approve" is not consent.

### First attempt: MCP elicitation forms (abandoned)

MCP lets a server ask the user a question through the client (**elicitation**), and the first version of this decision used it: a decision tool sent a confirmation form, and only an Accept recorded the decision. The live test in VS Code showed why that can't work there:

- **The form is never shown.** The VS Code extension runs Claude Code in print mode, and its log reads "Elicitation request received in print mode". The client answered every form by itself within about 15 ms.
- **The automatic answer was "decline".** It was indistinguishable from a real one, so the server recorded four declines in the user's name that they never made. The audit log is append-only, so they remain, each followed by a `system / audit_correction` entry.
- **The answer is never proof of a person.** Claude Code's code also lets an *elicitation hook* answer a form, "accept" included, before any form is shown.

A form's answer doesn't prove a person saw the form, and nothing the server receives can tell the difference.

## Decision

- **The user's steps are MCP tools:**
  - **Read:** `list_proposals` and `show_proposal`.
  - **Act:** `apply_proposal` and `export_deck`.
  - **Decide:** `approve_proposal`, `reject_proposal` and `approve_export`, plus `archive_deck` (#112), because a deck shouldn't disappear from the user's lists without them seeing it. Its summary is *"Archive deck '<name>'"*. Renaming and unarchiving hide nothing, so they aren't decisions: they're audited as the client's actions.
- **Decision tools are marked as requiring user interaction:** `_meta["anthropic/requiresUserInteraction"] = true`. For a tool marked this way, Claude Code (documented, and confirmed in its code):
  - **always shows the user its Allow/Deny permission prompt.** The VS Code extension does show these prompts.
  - **never approves it automatically:** not through allow rules, "always allow", auto mode, bypass mode, hooks, or a permission-prompt tool. It's on the list of "actions no mode auto-approves".
  - **denies it in modes that can't prompt,** so the failure is safe: nothing is recorded.
- **The prompt shows a summary the server wrote.** A permission prompt displays the call's arguments, so each decision tool takes a `confirming` argument that must equal the server's own one-line summary of the decision, word for word. For example: *"Approve: Bard, King of Dale as commander, 100 cards, for deck 'MCP test'"*.
  - **Where the model gets it:** from `show_proposal`, or from `apply_proposal` for an export.
  - **What a mismatch does:** the call is refused, with the correct text.
  - **Why:** the text the user allows is the database's account, not something the model could slant.
- **Only clients known to honour the marking may decide** (`MCP_DECISION_CLIENTS`, default `claude-code`), going by the name the client gives when it connects. Others are sent to the CLI or the API.
- **Each decision records where it was made:** `via: {channel: mcp, client: …}` in its audit entry.
- **Consent is never an argument.** Extra arguments such as `actor` are rejected.
- **Apply and export are unchanged:** they still refuse without the recorded approval (ADR 0013).
- **Proposal results give the proposal's ID** over MCP. The built-in agent keeps "proposal N", so its recordings still replay.

## Alternatives

**Elicitation forms.** This was the first version, abandoned for the reasons above. It's the right protocol mechanism in principle, but in this client it doesn't reach a person.

**A human-speed floor:** treat answers faster than a person could read as "not shown". It would stop false decisions, but it would still leave the user unable to decide anywhere in VS Code, which was the whole goal.

**Keep decisions out of MCP** (ADR 0014). It's the strictest option, but it makes MCP a drafting tool only, which the owner rejected.

**A per-tool "ask" rule in the project's `.claude/settings.json`.** Ask rules also beat "always allow", but they live in a file the model can edit in an agentic session, while the marking comes from the server itself.

## Consequences

- **A deck can be drafted, refined, approved, applied and exported without leaving Claude Code in VS Code.** Every decision is a click on Allow in a prompt that shows the server's summary of it.
- **The boundary is Claude Code's permission system,** the same one that stops the model running shell commands unasked. That's an honest fit: in an agentic session, the model could also run `mtg-advisor approve` through the shell, and only that same permission system stands in the way.
- **The server still can't see the prompt.** It trusts the client to honour the marking, and a client could misstate its name. The allow-list and the recorded `via` make the trust explicit and visible in the audit trail. The CLI and API don't depend on it.
- **Other clients, Claude Desktop included, can't decide until they're verified** and added to `MCP_DECISION_CLIENTS`.
- **Two calls per decision:** `show_proposal`, then the decision with the summary text. That's a small cost for the user seeing exactly what they allow.
