# 0015 - Approval through MCP elicitation

**Status:** Accepted, 2026-10-05
**Applies to:** `mtg_deck_advisor.mcp_server`
**Amends:** [ADR 0014](0014-interfaces-background-runs-a-thin-cli-and-mcp.md), which gave the MCP server no way to approve

## Context

ADR 0014 exposed only the agent's tools over MCP, so a deck drafted in Claude Code had to be approved, applied and exported in the CLI or the API. In use, that meant switching tools mid-build, and the owner asked for a deck to be buildable end to end from one place.

The obstacle is GRD-2: a side effect needs **the user's** approval. If a tool call were enough, the model could approve its own proposal, or be talked into it by injected card text. "The model called approve" is not consent.

MCP has a mechanism for exactly this: **elicitation**. A server asks the *user* a question through the client's interface, and the client returns the user's answer. The model sees neither the form nor the answer.

## Decision

- **The user's steps become MCP tools:**
  - **Read:** `list_proposals` and `show_proposal`.
  - **Act:** `apply_proposal` and `export_deck`.
  - **Decide:** `approve_proposal`, `reject_proposal` and `approve_export`.
- **A decision tool doesn't decide. It asks.** Calling one makes the server send an elicitation form, and the decision is recorded (`actor = user`) **only if the user accepts**.
  - **Declined:** audited as the user's (`approval_declined` and so on), and nothing changes.
  - **Cancelled:** the form was dismissed or never shown, so the user decided nothing. It's audited as the **system's** (`approval_cancelled`), never the user's, and the model is told a form may not have appeared. Every answer is logged with the action the client returned.
  - **No elicitation support:** the call is refused, and the user is sent to the CLI.
- **The form is the server's account, not the model's.** It is written from the database (commander, card count, the cards added and removed, the version to export). The model's rationale is left out, so the text the user consents to can't be slanted by the model. For a rejection, the form shows the reason the model gave, and the user can replace it.
- **Consent is never an argument.** The decision tools' schemas take only the proposal or deck ID (and a reason for a rejection). Extra arguments are rejected before anything is asked, so `"confirmed": true` gets nowhere.
- **Apply and export are unchanged.** They still refuse without the recorded approval (ADR 0013), so an MCP client can call them freely.
- **Both protocol eras are supported:**
  - **Handshake era:** where the server can send the client a request mid-call, it sends the form and waits.
  - **2026-07-28 revision:** the server returns *input required*, and the client calls again with the answer. The state handed back is a fingerprint of the exact message shown, and the answer is recorded only if the server would show the same message now. If the proposal changed in between, the user is asked again.
- **Proposal results give the proposal's ID** over MCP, so the client can show, approve or apply it. The built-in agent keeps "proposal N", so its recordings still replay.

## Alternatives

**Rely on the client's tool-permission prompt.** Claude Code asks before running a tool, so "approve_proposal?" would reach the user. But the prompt's meaning belongs to the client: users choose "always allow", other clients may not prompt at all, and the server can't tell either way. Elicitation is a question the server itself asks and the answer is explicit.

**Keep approval out of MCP** (ADR 0014). It's the strictest option, but it makes MCP a drafting tool only, which the owner rejected after trying it.

**A one-time code** that the user reads from the CLI and passes to the tool. It proves a human was involved, but it still needs the second tool, which is the very switch this decision removes.

## Consequences

- **A deck can be drafted, refined, approved, applied and exported without leaving Claude Code.** Every decision is still the user's, recorded and audited like one made in the CLI.
- **The guarantee now depends on the client.** Elicitation means "the client asked the user". A client that answers forms on its own, or shows them to its model instead of the user, would turn a model's choice into a recorded "user" decision. The CLI and API don't have this dependency. The audit entries name the client, and the record makes clear which path a decision took (`mcp:<client>` runs), but the server can't verify that a human answered.
- **Found in the first live test:** the Claude Code VS Code extension advertises form support but answered without showing the form, and the first version of this server recorded those answers as the user's declines (two false `rejection_declined` entries; the audit log is append-only, so they remain). Cancels are now the system's, as above. A client that sends "decline" without asking is the remaining risk described here, and the server can't detect it.
- **Headless sessions can't decide.** `claude -p` has no one to show a form to and declines it. That's the safe failure, but it means automation stops at "proposed".
- **Two protocol paths to maintain,** both covered by tests that run every decision test in each mode.
