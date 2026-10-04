# Agent live runs: 2026-10-04

The first real runs of the Milestone 5 agent (#91), on Claude Sonnet 5.5 with `effort: medium`, against the local stack:
- the full catalogue
- `qwen3-embedding:8b` search with the `qwen3:8b` reranker
- the 300-card pool `evals/datasets/pool_300.txt`

Each paid run was estimated and approved by the owner first. Each proposal was approved by the owner before it was applied. Every response was recorded (`RECORD_RESPONSES=true`) for the no-key demo.

Commands: `scripts/agent_run.py`.

## Results

| Run | Status | Turns | Tool calls (ok / error / rejected) | Cost | Wall time |
|---|---|---|---|---|---|
| Draft (no request) | completed | 6 | 9 / 2 / 0 | $0.1321 | 76 s |
| Refine (swap three weak cards, add removal if any) | completed | 5 | 6 / 1 / 1 | $0.0669 | 38 s |
| Rules: hybrid mana and color identity | answered | 2 | 1 / 0 / 0 | $0.0065 | 8 s |
| Rules: commander to graveyard or exile | answered | 2 | 1 / 0 / 0 | $0.0047 | 5 s |
| Rules: commander damage | answered | 2 | 1 / 0 / 0 | $0.0031 | 4 s |
| Rules: proxy policy (not in the rules) | not found | 2 | 1 / 0 / 0 | $0.0035 | 4 s |
| **Total** | | | | **$0.2168** | |

- **Milestone 5 spend:** $0.221 of the $10 budget, counting the $0.004 tool-calling smoke test.
- **Prompt caching does most of the work.** In the draft, about 60k input tokens were read from cache and about 28k written, while 14 were billed at the full input rate. Output, about 5k tokens of thinking and text, was most of the cost.

## What happened

**Draft.**
- The agent asked for 40 search results on its first turn, over the tool's limit of 25. Both calls came back as error results naming `k`, and it retried within the limit (AGT-4, live).
- It researched the pool and chose **Adeline, Resplendent Cathar**, mono-white go-wide. Its one `propose_deck` passed every check on the first try: 38 Plains and 61 pool cards.
- Its closing summary flagged its own weak spots: thin removal, filler cards, and an off-plan infect creature. The owner approved it, and it was applied as version 1.

**Refine.**
- Asked to replace three weak cards and add removal, the agent first proposed only the removals. That was rejected for leaving 97 cards. In the same turn it proposed the full swap, which was legal.
- It reported, correctly, that the pool had no unused removal worth adding.
- The owner approved it, and it was applied as version 2.

**Export.**
- An export attempted before approval was refused, and the refusal was audited.
- After the owner approved the export of version 2, it produced a 100-card plain decklist.
- **Audit trail, for both proposals:** `propose` (agent), `approve` (user), `apply` (system).
- **Audit trail, for the deck:** `export_refused`, `approve_export`, `export`.

**Rules.**
- Three questions were answered with correct citations, each to a rule the run had retrieved: 903.5c, 202.2d, 903.9a and 903.9b, 704.6c, 903.10a, and others.
- The proxy question returned "NOT FOUND" instead of an invented policy. Proxies aren't in the Comprehensive Rules.

**Replay.**
- With `MODEL_PROVIDER=replay` and no key, the draft replays exactly: 6 turns and the same proposal, for $0 in 19 s, with real local search and reranking. A rules question also replays.
- This works because tool results are deterministic: proposals are numbered within the run, not shown by their random IDs.

## Findings

- **Done-when met (#91):**
  - The real draft ended in a valid 100-card deck.
  - The refine went through approval, apply, the audit trail and export.
- **Cost is far below the estimate:** $0.13 against the $0.30–0.60 estimated for a draft. One whole-deck proposal keeps the turns down, and caching makes later turns cheap.
- **The quality of a draft is limited by the pool.** A random 300-card pool gives a playable but thin deck, and the agent says so.
  - Judging deck quality, not just legality, is Milestone 7's evaluation. This is one run, not a measurement.
- **Open questions for later:**
  - Should `search_pool`'s `k` limit go up from 25? The agent asked for 40 twice. Changing it changes the tool schema, which would invalidate these recordings.
  - The "Unique Charmed Pants" pick: a Stickers card the agent itself flagged. Is it a card most players would want suggested?
