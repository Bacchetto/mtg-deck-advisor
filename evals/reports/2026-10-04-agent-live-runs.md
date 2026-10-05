# Agent live runs: 2026-10-04

The first real runs of the Milestone 5 agent (#91), on Claude Sonnet 5.5 with `effort: medium`, against the local stack:
- the full catalogue
- `qwen3-embedding:8b` search with the `qwen3:8b` reranker
- the 300-card pool `evals/datasets/pool_300.txt`

Each paid run was estimated and approved by the owner first. Each proposal was approved by the owner before it was applied.

Commands: `scripts/agent_run.py`, since replaced by the `mtg-advisor` CLI (Milestone 6). The round 2 refine's request was: "Replace Tine Shrike, Curse of Conformity and Dawn-Blessed Pennant with cards from my pool that fit the go-wide plan better, and add removal if the pool has any you left out. Keep it at 100 cards."

There were two rounds:
- **The first round** found a schema problem: the tool's limit on how many cards a search returns was invisible to the model.
- **The second round** ran after the fix. Its responses are the recordings in `recordings/`, used for the no-key demo.

## Results

| Run | Status | Turns | Tool calls (ok / error / rejected) | Cost | Wall time |
|---|---|---|---|---|---|
| **Round 1** | | | | | |
| Draft (no request) | completed | 6 | 9 / 2 / 0 | $0.1321 | 76 s |
| Refine (swap three weak cards, add removal if any) | completed | 5 | 6 / 1 / 1 | $0.0669 | 38 s |
| **Round 2 (recorded)** | | | | | |
| Draft (no request) | completed | 5 | 6 / 0 / 1 | $0.1173 | 51 s |
| Refine (swap three weak cards, add removal if any) | completed | 4 | 6 / 0 / 0 | $0.0649 | 19 s |
| **Rules (recorded in round 1, still valid)** | | | | | |
| Hybrid mana and color identity | answered | 2 | 1 / 0 / 0 | $0.0065 | 8 s |
| Commander to graveyard or exile | answered | 2 | 1 / 0 / 0 | $0.0047 | 5 s |
| Commander damage | answered | 2 | 1 / 0 / 0 | $0.0031 | 4 s |
| Proxy policy (not in the rules) | not found | 2 | 1 / 0 / 0 | $0.0035 | 4 s |
| **Total** | | | | **$0.3990** | |

- **Milestone 5 spend:** $0.403 of the $10 budget, counting the $0.004 tool-calling smoke test.
- **Prompt caching does most of the work.** In the round 2 draft, about 50k input tokens were read from cache and about 22k written, while 12 were billed at the full input rate. Output, about 5k tokens of thinking and text, was most of the cost.

## What happened

**Round 1: draft.**
- On its first turn the agent asked `search_pool` for 40 cards, twice in parallel. The limit was 25, so both calls came back as error results naming `k`, and it retried with 25 (AGT-4, live).
- It chose **Adeline, Resplendent Cathar**, mono-white go-wide. Its one proposal passed every check on the first try. The owner approved it, and it was applied.

**Round 1: refine.**
- It asked for 30 cards once (an error), then proposed only the removals. That was rejected for leaving 97 cards, and in the same turn it proposed the full swap, which was legal.
- The owner approved it, it was applied, and the export round trip completed.

**The limit problem.**
- Strict tool schemas can't carry `maximum`, `maxLength` and similar constraints, so `strict_schema` removed them. **The model was never told the limit;** it found out from the error.
- **The fix:** dropped constraints are now restated in the field's description ("How many cards to return. (minimum 1, maximum 40)"). The owner also raised the limit to 40.
- **A side effect:** the tool schema is part of each recording's key, so the round 1 draft and refine recordings stopped replaying. They were deleted and re-recorded. The rules recordings use only `search_rules`, which didn't change, so they still replay.

**Round 2: draft.**
- It searched with k=40, 20, 30 and 40, with no errors, and again chose **Adeline**.
- Its first proposal was rejected for having 99 cards, a miscount. It fixed that on the next turn.
- The owner approved it, and it was applied as version 1.

**Round 2: refine.**
- Asked to replace three filler cards and add removal, it swapped Tine Shrike, Curse of Conformity and Dawn-Blessed Pennant for Loran of the Third Path, Molten-Tail Masticore and Alpha Myr.
- It reported that the pool had no other removal left.
- The owner approved it, and it was applied as version 2.

**Round 2: export.**
- An export attempted before approval was refused, and the refusal was audited.
- After the owner approved the export of version 2, it produced a 100-card plain decklist.
- **Audit trail, for both proposals:** `propose` (agent), `approve` (user), `apply` (system).
- **Audit trail, for the deck:** `export_refused`, `approve_export`, `export`.

**Rules.**
- Three questions were answered with correct citations, each to a rule the run had retrieved: 903.5c, 202.2d, 903.9a and 903.9b, 704.6c, 903.10a, and others.
- The proxy question returned "NOT FOUND" instead of an invented policy. Proxies aren't in the Comprehensive Rules.

**Replay.**
- With `MODEL_PROVIDER=replay` and no key, the round 2 draft replays exactly: 5 turns, for $0 in 23 s, with real local search and reranking. The rules questions replay too.
- This works because tool results are deterministic: proposals are numbered within the run, not shown by their random IDs.

## Findings

- **Done-when met (#91), twice:**
  - Each real draft ended in a valid 100-card deck.
  - Each refine went through approval, apply, the audit trail and export.
- **Cost is far below the estimate:** $0.12–0.13 against the $0.30–0.60 estimated for a draft. One whole-deck proposal keeps the turns down, and caching makes later turns cheap.
- **Errors are cheap and recovered from.**
  - Over four deck runs the agent made three argument errors and three illegal proposals: one invalid change set, and two counting slips at 97 and 99 cards. Every one was fixed within the run.
  - The validator, not the model, kept the decks at exactly 100.
- **Limits the model can't see are a bug class.** Any constraint strict mode drops must reach the model some other way. It is now done generally in `strict_schema`, so it covers every tool and structured output.
- **The quality of a draft is limited by the pool.** A random 300-card pool gives a playable but thin deck, and the agent says so.
  - Judging deck quality, not just legality, is Milestone 7's evaluation. These are two runs, not a measurement.
- **Picks the owner reviewed:** the agent added the Stickers card Unique Charmed Pants in round 1. The owner confirmed that's fine: it's legal and in the pool.
