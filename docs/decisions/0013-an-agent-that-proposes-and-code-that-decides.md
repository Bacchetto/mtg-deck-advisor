# 0013 - An agent that proposes, and code that decides

**Status:** Accepted, 2026-10-04
**Applies to:** `mtg_deck_advisor.agent`, `guardrails.proposals`, `guardrails.approvals`, `guardrails.untrusted`, `deck.store`

## Context

Milestone 5 builds the product's core: a model that drafts a legal 100-card Commander deck from a user's card pool, refines it on request, and answers rules questions. The model is good at the open-ended part (choosing a commander, a plan and the cards to serve it) and unreliable at the parts with a right answer: counting to 100, staying in color identity, remembering which cards the user owns, citing rules it actually read.

The requirements put the line in the same place:

- **AGT-1 to AGT-4:** native tool calling, a bounded loop, state owned by code, errors returned to the model
- **GRD-1:** every proposal validated
- **GRD-2:** no side effect without an approval
- **GRD-3:** untrusted content
- **GRD-5:** an audit log
- **RAG-3 / RAG-4:** citations, and saying "not found"

The owner chose Claude Sonnet 5.5 as the agent's model, a $10 budget for live runs, and a whole-deck draft in one proposal.

## Decision

**The model proposes; code decides; the user approves.**

- **A loop in our code** (`agent/loop.py`), on the native tool calling of ADR 0012.
  - **Bounded:** at most `AGENT_MAX_TURNS` model calls, and a per-run cost cap checked against each call's worst case *before* it is made.
  - **Always recorded:** every run ends `completed`, `turn_limit`, `budget` or `error`, with its transcript, turns and cost saved. A run cut short keeps its proposals as partial results.
- **State lives in tables, not in the conversation** (AGT-3).
  - Pools, decks, and **immutable deck versions:** a trigger refuses edits, so a deck changes only by adding a version.
  - The agent reads state through tools and changes nothing directly.
- **Six tools.** Four read: `search_pool`, `get_card`, `search_rules`, `analyze_deck`. Two propose: `propose_deck` and `propose_changes`. Each task is offered only its own tools.
  - Each tool's Pydantic model is both the schema the model sees and the validator for its arguments.
  - An unknown tool, bad arguments, a tool from another task, or a failure inside a tool comes back as an **error result**, and the run continues (AGT-4).
- **Proposals are checked by code** (GRD-1).
  - Each proposal is applied to a copy of the deck and run through the Commander validator (ADR 0007).
  - Every cited rule and card must have appeared in this run's tool results (RAG-3).
  - A failing proposal is stored as `invalid`, with every problem sent back to the model to fix. A passing one waits as `pending`.
- **Side effects need an approval record, and have no tool** (GRD-2).
  - `approve`, `reject`, `apply` and `export` are service functions.
  - The `approvals` table accepts only `actor = 'user'` and can't be edited.
  - `apply` refuses without an approval, a second time, for a deck that has moved on since the proposal, or for a deck no longer legal: it re-checks rather than trusting the earlier check.
  - `export` covers only the exact version approved.
  - Every decision, action and refusal goes to the append-only `audit_log` (GRD-5).
- **Untrusted content is delimited, and harmless by construction** (GRD-3).
  - **Delimited:** card names, oracle text and rule text reach the model only inside `<untrusted>` blocks. Anything in the data that looks like a delimiter is escaped, so the data can't close its own block.
  - **Told:** the system prompt says delimited content is data, never instructions.
  - **Harmless by construction:** delimiting only lowers the odds that injected text is obeyed, so it isn't what the safety rests on. Even a model that obeys an injected "export the deck" can't do it, because nothing it can call has a side effect. A test plays exactly that model.
- **Rules answers are grounded by code** (RAG-3, RAG-4).
  - The answer must end with the rule numbers it relies on.
  - It reaches the user only if it cites at least one rule and every cited rule was retrieved in the run.
  - Otherwise it comes back as not found, with the reason. An honest "NOT FOUND" is a valid answer.

## Alternatives

**Let the model hold the deck in the conversation.** It's the simplest, and the most fragile. Long transcripts drift: cards get double-counted or forgotten, and nothing is checkable or recoverable after a crash. Keeping state in tables makes every version inspectable and every change auditable.

**Fine-grained tools** (`add_card`, `remove_card`). These are natural for an agent, but a 100-card draft becomes about 100 tool calls and turns. The owner chose one whole-deck proposal. Validation then sees the whole deck at once and reports every problem in one go, which suits how the validator already works (ADR 0007).

**A confirmation step instead of an approval record** (the agent asks "shall I apply?" and applies on "yes"). That puts the decision inside the conversation, where injected text or a misread reply can fake it. An approval made outside the agent, in a table the agent can't write to, can't be talked into existence.

**A judge model to check grounding.** A second model could judge whether an answer is supported, but that is another probabilistic check. Comparing cited rule numbers with retrieved ones is exact and free. It doesn't prove the answer *reads* the rules correctly; that is what Milestone 7's evaluation is for.

**The SDK's tool runner.** See ADR 0012: the limits, recording and error handling are the point, and they belong in our code.

## Consequences

- **The model can be wrong without the deck being wrong.** An illegal deck can't be applied, and an unapproved one can't be applied or exported.
- **Every run, tool call, proposal, decision and refusal is in the database under a trace ID,** so "why does my deck have this card?" has an answer.
- **The agent may take extra turns fixing rejected proposals.** Each costs money, which the turn and cost limits bound.
- **Citations are checked for provenance, not for meaning.** A cited rule was retrieved, but it may not say what the answer claims. Measuring that is Milestone 7's job.
- **Only Claude can run the agent for now** (ADR 0012), and live runs cost money. Tests script the model with `FakeProvider`, so CI stays free.
- **Recorded runs replay only if tool results repeat exactly.** Proposals are therefore numbered within the run, not shown by their random IDs.
