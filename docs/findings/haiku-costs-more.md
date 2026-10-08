# The cheaper model cost more per deck

**Finding:** drafting Commander decks, Claude Haiku 4.5 cost **46% more per deck** than Claude
Sonnet 5.5 ($0.175 against $0.120), though its tokens cost exactly half as much. It also built
worse decks and took three times as long.

This is from the Milestone 7 eval. The run files are committed, and the numbers below are
computed from them:
- [`evals/runs/deck_tasks/2026-10-07T173152-sonnet.json`](../../evals/runs/deck_tasks/2026-10-07T173152-sonnet.json)
- [`evals/runs/deck_tasks/2026-10-07T213557-haiku.json`](../../evals/runs/deck_tasks/2026-10-07T213557-haiku.json)

The reports for those runs are [Sonnet's](../../evals/reports/2026-10-07-deck-tasks.md) and
[Haiku's](../../evals/reports/2026-10-07-deck-tasks-2.md).

## The numbers

The same 8 drafting tasks (T01-T08), the same agent, tools, prompt and pools. Only the model differs.

| Per draft | Sonnet 5.5 | Haiku 4.5 |
|---|---|---|
| Price per million tokens (input / output) | $2 / $10 | **$1 / $5** |
| Turns (model calls) | **4.8** | 17.1 |
| Proposals rejected by the validator | **0.5** | 5.3 |
| Cost per turn | $0.025 | **$0.010** |
| **Cost per deck** | **$0.120** | $0.175 |
| Median time | **66 s** | 212 s |
| Legal deck at the end | 100% | 100% |
| Deck quality (1-5, Opus grader) | **3.30** | 2.87 |
| Mana base (1-5) | **3.38** | 1.75 |

**Haiku cost more on every one of the 8 tasks,** from 4% more (T05) to 132% more (T06, where it took
27 turns and had 9 proposals rejected).

## Why

**The cost of an agent run is price per token × tokens per turn × turns.** A cheaper model only
wins if it doesn't take more turns, and here it took 3.6 times as many.

- **Rejections drove the extra turns.**
  - **Code checks every proposal:** the agent can't save a deck, it only proposes one, and code checks it against the Commander rules and the user's pool ([ADR 0013](../decisions/0013-an-agent-that-proposes-and-code-that-decides.md)).
  - **Sonnet usually proposed a legal deck first time:** 4 rejections in 8 drafts.
  - **Haiku proposed an illegal deck about five times per draft.** The run files keep the count, not the problems.
  - **Each rejection costs a turn:** it goes back to the model with the problems listed, and each fix costs another turn, often with more searches first.
- **Each turn cost Haiku more than half of Sonnet's turn.**
  - **Expected:** a turn at half the token price would cost half as much.
  - **Measured:** Haiku's turns cost 40% of Sonnet's, not 50%, which on this rough model means about 20% fewer tokens per turn. Its turns are cheaper, but nowhere near enough to make up for 3.6 times as many.
  - **The likely cause is the growing transcript.** A tool-calling agent re-sends the whole conversation every turn: the request, every search result, every rejected proposal and its problems. A long run pays for that history again on each turn, so its later turns cost more than its early ones.
- **The guardrails held either way.** Both models ended with a legal deck every time, because
  code, not the model, decides what's legal. The cheaper model's mistakes didn't produce worse
  outcomes on legality, only higher cost and latency. They did produce worse decks: Haiku's mana
  bases scored 1.75 out of 5.

**The lesson:** for an agent, compare models on **cost per completed task**, measured on the real
task. Token prices predict it poorly, because the number of turns depends on the model's skill,
and the guardrails turn each mistake into another turn.

## Caveats

- **It's a small sample:** 8 drafts per model, one task set, one run each. The direction is
  consistent across all 8 tasks, but the sizes of the differences are rough.
- **One Sonnet run ended early.** Sonnet's T07 hit its budget after 3 turns, at $0.071. Without
  it, Sonnet still averages 5.0 turns and $0.126 per draft.
- **The two refine tasks (T09, T10) ran only on Sonnet,** so they're left out here.
- **It's about this agent.** A validator that rejects illegal proposals is what makes mistakes
  expensive. An agent without one would look cheaper on Haiku, and be wrong more often.
- **Rules answers point the other way.** For one-shot rules questions, with about two turns
  each, Haiku *was* cheaper: $0.16 against $0.25 for 55 questions. Its citations were also more
  precise, but it answered 7 of 10 questions the rules don't cover instead of saying "not found"
  ([Milestone 7 report](../../evals/reports/2026-10-08-milestone-7.md)). Token prices predict
  cost well only when the number of turns is fixed.
- **The token counts weren't kept.** The eval ran against a throwaway database, so the costs
  here are from the run files, and the per-turn explanation is inferred from cost per turn, not
  measured per token.
