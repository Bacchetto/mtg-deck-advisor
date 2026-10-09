# Deck grader consistency on refines, 2026-10-09

How much does the deck grader (Opus 5.5, `evals/rubrics/deck_quality.md`) move on its own? Part of #136, to find out where the noise in refine scores comes from.

**Method:** the 12 refine decks from the two regraded runs (the six refines before and after goal-checking, with the change shown) were graded again with nothing changed: the same deck text, rubric and model.
- [`2026-10-08T222201-sonnet-regraded.json`](../runs/deck_tasks/2026-10-08T222201-sonnet-regraded.json) → [`2026-10-09T172713-sonnet-regraded-regraded.json`](../runs/deck_tasks/2026-10-09T172713-sonnet-regraded-regraded.json)
- [`2026-10-08T222354-sonnet-regraded.json`](../runs/deck_tasks/2026-10-08T222354-sonnet-regraded.json) → [`2026-10-09T172903-sonnet-regraded-regraded.json`](../runs/deck_tasks/2026-10-09T172903-sonnet-regraded-regraded.json)

The grading cost $0.76.

## Results

| Criterion | Same score both times | Mean absolute change | SD of the change |
|---|---|---|---|
| **Fit to the request** | **92%** (11 of 12) | 0.08 | 0.29 |
| Plan coherence | 100% | 0.00 | 0.00 |
| Card draw | 100% | 0.00 | 0.00 |
| Interaction | 100% | 0.00 | 0.00 |
| Ramp | 67% | 0.33 | 0.60 |
| Mana base | 58% | 0.42 | 0.51 |
| Quality (the mean) | 33% | 0.14 | 0.18 |

- **Fit is stable.** One deck in 12 moved (T12, 5 → 4).
- **Mana drifts down.** Every mana change was one point lower on the second grading (5 of 12).
- **Quality** moves by about 0.14 between gradings, from the ramp and mana changes.

## What it means for measuring refines

- **The agent, not the grader, is the main source of noise in refine fit.** Before and after goal-checking, the per-task change in fit had an SD of about 0.75. The grader's own SD of change is about 0.3. Most of the spread is the agent making different changes on different runs.
- **So more tasks, repeated runs, and code-checked success (pass or fail) matter more** than grading each deck several times.
- **Pairwise grading** is still useful for splitting ties within a 1-5 score, but grader noise alone doesn't justify it.
- **Quality comparisons smaller than about 0.2** are within the grader's own noise.
