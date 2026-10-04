# 0012 - Native tool calling, with each provider turn kept verbatim

**Status:** Accepted, 2026-10-04
**Applies to:** `mtg_deck_advisor.llm` (`types`, `anthropic`, `client`, `fake`, `replay`)

## Context

Milestone 5's agent needs **native tool calling (AGT-1)**: the model asks for a tool by name with JSON arguments, code runs it, and the result goes back in the next request. The model layer from ADR 0008 carried text only.

The agent runs on Claude Sonnet 5.5 with thinking on. Two API rules shape the design:

- **Thinking blocks on a tool-use turn have to be sent back exactly as they came,** signature included. A turn rebuilt from its text and tool calls loses them, and the model loses the reasoning that led to the call.
- **Forced tool choice (`tool_choice: any` or a named tool) is rejected** with thinking on. The model has to be left to decide when to call a tool.

Everything else in ADR 0008 still applies: one interface, provider swapping, cost caps, a record of every call, and no paid calls in tests.

## Decision

- **Tool calling in the project's own types.**
  - `ToolSpec` (name, description, JSON schema) goes on the request.
  - `ToolCall` (ID, name, arguments) comes back on the response.
  - `ToolResult` (call ID, content, `is_error`) goes back in the next user turn.
  - `Message` carries text plus tool calls (assistant) or tool results (user). A validator stops either side carrying the other's parts.
- **Each assistant turn keeps `provider_content`:** the provider's own blocks for that turn, verbatim and opaque to the rest of the app. `ModelResponse.as_message()` keeps them on the turn the caller appends to the conversation.
  - **Anthropic:** a turn with `provider_content` is sent back as exactly those blocks. A turn without them (scripted, or from another provider) is rebuilt from its text and tool calls.
- **Strict tools, automatic choice.** Tools are sent with `strict: true` and the same schema clean-up as structured output, so arguments always parse. `tool_choice` is never set. Arguments that are valid JSON but wrong for the task are still checked by the tool's own Pydantic model (Milestone 5, sub-issue 3).
- **All tool results go back in one user turn,** with `is_error` set on failures, so the model can recover from a bad call within the same run (AGT-4).
- **Ollama refuses tools** with a `ModelCallError` rather than dropping them and answering as if none were offered. Local models only tag and rerank.
- **Replay and recording stay compatible.** Empty tool fields are left out of the replay key and the recorded request, so every existing recording replays unchanged (a test pins one key). A response with tool calls is recorded as JSON holding the text and calls, so the turn can be reconstructed from its trace ID (OBS-1).
- **Costing counts the tools.** The worst-case estimate includes tool specs, tool arguments, results and kept blocks, so the per-run cap still holds on long agent transcripts.

## Alternatives

**The SDK's tool runner.** It runs the loop and calls Python functions directly. But the loop is where the project's guarantees live: turn and cost limits, approval-free tools only, recording each call, and returning errors to the model. Those belong in our code (ADR 0008), behind a provider that can be faked.

**Store only text and tool calls, and rebuild every turn.** Simpler types, but thinking would be lost on every tool turn: worse decisions, and against the API's requirement.

**A provider-neutral content-block model** (text, thinking, tool use and so on, all typed). More general, but the project has one provider that can call tools. The opaque `provider_content` field keeps provider details out of the rest of the app; a second tool-capable provider can add its own blocks the same way.

## Consequences

- **The agent gets native tool calls, with thinking kept across turns,** and the loop stays in code the tests can script with `FakeProvider` and `tool_call_reply`.
- **Transcripts must be append-only.** Editing or reordering an earlier turn would invalidate its kept thinking. The agent loop has to grow the message list, never rewrite it.
- **`provider_content` ties a turn to the provider that produced it.** Switching providers mid-conversation falls back to rebuilding turns from text and calls, without thinking.
- **Requests get larger:** kept thinking and tool results count as input on every later turn. The cost cap includes them, so a long run stops cleanly instead of overspending.
- **Only Claude can run the agent for now.** Running it on a local model needs Ollama tool support first, measured as in ADR 0009.
