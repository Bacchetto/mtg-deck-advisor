# The hand-built loop against the Claude Agent SDK

**Finding:** on the same tools, model and prompt, the Agent SDK built decks about as well as the
hand-built loop, for about the same cost: legal decks on all 8 drafts, quality 3.21 against 3.48,
and $0.112 against $0.122 a deck. The difference is in what the project can see and control. The
SDK's model calls bypass the project's client, so they aren't recorded, can't be replayed and are
capped only after they're spent. On one task the model sent malformed tool calls 16 times, and
Claude Code turned them away before they reached the project's code. **The project keeps the
hand-built loop.**

This is AGT-6 (#153): a second implementation of the deck agent on an agent framework, compared
with the hand-built loop. The framework is the
[Claude Agent SDK](https://pypi.org/project/claude-agent-sdk/) for Python (0.2.168), which runs
Claude Code in a subprocess as the agent loop.

## What was compared

Both agents ran the same 8 drafting tasks (T01-T08), one run each, graded by the same Opus grader
against [the same rubric](../../evals/rubrics/deck_quality.md). Only the loop differed:

- **The tools are the same code.** The SDK agent's tools are served from an in-process MCP server
  whose handlers call the same `execute()` the hand-built loop calls
  ([`agent/sdk.py`](../../src/mtg_deck_advisor/agent/sdk.py)). Validation, the guardrails,
  proposals and tool-call recording are identical.
- **The same model, prompt, effort and limits:** Sonnet 5.5, the shipped system prompt, medium
  effort, 30 turns and the $1 cost cap.
- **The same code otherwise.** The loop ran at `180349c` and the SDK agent at `576b821`. Between
  them only the SDK adapter changed (it gained the loop's effort setting), so the hand-built code
  is identical.

The run files and reports are committed:
- [The hand-built loop](../../evals/runs/deck_tasks/2026-10-19T202743-sonnet.json) ([report](../../evals/reports/2026-10-19-deck-tasks.md))
- [The Agent SDK](../../evals/runs/deck_tasks/2026-10-19T203642-sonnet-sdk.json) ([report](../../evals/reports/2026-10-19-deck-tasks-2.md))

## The numbers

| Per draft | Hand-built loop | Agent SDK |
|---|---|---|
| Legal deck at the end | 100% | 100% |
| Deck quality (1-5, Opus grader) | **3.48** | 3.21 |
| Agent cost per deck | $0.122 | **$0.112** |
| Median time | 66 s | 65 s |
| Model calls | 4.6 | 6.5 (4.6 without T03) |
| Proposals rejected by code (all 8 drafts) | 4 | 4 |
| Malformed tool calls turned away before the project's code | 0 | 16 (all in T03) |

**Quality, task by task:** the SDK agent scored higher on 2 tasks, lower on 4 and the same on 2.
Most of the gap is T03:

| Task | Loop | SDK |
|---|---|---|
| T01 | 2.20 | 2.20 |
| T02 | 2.67 | 2.83 |
| T03 | **3.33** | 2.33 |
| T04 | **3.20** | 2.80 |
| T05 | 3.00 | **3.17** |
| T06 | **4.60** | 4.00 |
| T07 | 4.17 | 4.17 |
| T08 | **4.67** | 4.17 |

Without T03, the SDK agent is 0.17 lower on average. That's within what the same agent varies by
between runs: the loop scored 3.30 on these tasks in Milestone 7 and 3.48 here. One run each
can't separate the two on quality.

## What happened in T03

The model called the pool search 16 times with input that isn't JSON, leaving a value out:

```
{"query": "artifact", "color_identity_within": , "k": 40}
```

Claude Code rejected each one itself, with an `InputValidationError`, and the model tried again.
The project's tool code never saw these calls, so **they're in no tool-call record:** the run
file shows 5 successful tool calls and no errors, for a run that took 20 model calls. Only the
SDK's own transcript, stored with the run, shows what happened. Meanwhile the run searched less
than it meant to and built a deck around a different commander, which scored a point lower.

The hand-built loop has never sent malformed input in these runs. A likely reason, not verified,
is that Claude Code streams tool input and parses it itself, where the loop's API calls return
the input already parsed.

## What each one is like to own

| | Hand-built loop | Agent SDK |
|---|---|---|
| **Code the project owns** | 181 lines ([`agent/loop.py`](../../src/mtg_deck_advisor/agent/loop.py)) | 226 lines of adapter ([`agent/sdk.py`](../../src/mtg_deck_advisor/agent/sdk.py)) |
| **What it runs on** | The project's `ModelClient`, over the Anthropic SDK | A bundled Claude Code binary (272 MB) in a subprocess. So it's an optional extra, not in the image |
| **Cost cap** | Checked **before** each call, against its worst case, so a run can't overspend | Checked **after** each turn ("the query will stop if this budget is exceeded"), so a run can go over by a turn |
| **Each model call** | Recorded in `model_calls`: tokens, cost, latency and outcome | Not recorded: only the run's totals come back, as Claude Code computes them |
| **Offline replay** | Yes: the demo and CI replay recorded responses with no API key | No: the model calls never pass through the project's client |
| **Tests** | The whole loop, with a scripted fake model | Only the adapter: what the loop does is tested live |
| **Locking it down** | Nothing to do: the loop only knows the deck tools | Claude Code is a general agent. The adapter turns off its built-in tools (shell, files, web) and the user's settings and CLAUDE.md, and gives it its own config and working directories. The task's tools are on an allow-list, and a permission callback denies anything else |
| **Turns** | One per model call | Claude Code's own count, higher than its model calls (9.5 against 6.5 here) |
| **Refusals** (#137) | Its own `refused` status and message | An error result, like any other |
| **Failures** | Each one handled by the loop | Some come back as exceptions: when the account ran out of credit, as `ResultError`, with no result |

## Why the project keeps the hand-built loop

The SDK agent did the job about as well. What it gave up is what this project is built on:

- **Spending is capped before it happens** (AGT-2). The SDK finds out after a turn that it's over
  budget.
- **Every model call and tool call is recorded** (OBS-1). The SDK's model calls never reach the
  recorder, and in T03, 16 failed tool calls were invisible to the project's records.
- **Recorded calls can be replayed.** The free demo and CI replay them with no API key
  ([ADR 0008](../decisions/0008-one-model-interface-owned-by-the-project.md)). The SDK agent can't
  be replayed.
- **It's tested without a model.** With the SDK, only the adapter can be.

The SDK would suit an agent that needs what Claude Code already has: files, a shell, sub-agents
and context management. This agent needs a handful of tools of its own and tight control around
them, which a short loop gives directly. The adapter that runs it on the SDK, locked down, is
longer than the loop itself.

## Caveats

- **It's a small sample:** 8 drafts, one run each. Cost and time are close enough to call even.
  The quality gap is within run-to-run variation, except for T03.
- **The SDK's costs are its own figures.** Its model calls don't pass through the project's
  client, so they can't be checked against the project's records.
- **Two SDK tasks were run again.** T07 and T08 first failed because the API account ran out of
  credit, before any real work. They were run again after it was topped up, at the same commit,
  and merged into the run file.
- **Drafts only.** The 18 refine tasks weren't run, to keep the cost down. Refines are where
  `analyze_deck` and the goal checks matter (#136), and they weren't compared.
- **One version of a fast-moving SDK.** Its defaults (streaming tool input, turn counting,
  the budget check) may change.
