# Deck tasks, 2026-10-07 18:25 UTC

Tasks: `evals/datasets/deck_tasks.jsonl` (10). Quality graded by claude-opus-5-5 with [the rubric](../rubrics/deck_quality.md); legality is checked by code. Run again and merged: T08, T09, T10.

## Variants

| Variant | Model | Prompt | Card search | Rules search | Rerank | Commit |
|---|---|---|---|---|---|---|
| sonnet | claude-sonnet-5-5 | default | hybrid | vector | yes | 60e5fc9 |

## Results

| Variant | Cases | Success | Total cost | Mean cost | Median latency | Mean turns | Tool errors | Rejected proposals | Skipped | Grading cost |
|---|---|---|---|---|---|---|---|---|---|---|
| sonnet | 10 | 100% | $1.0523 | $0.1052 | 62.5 s | 4.8 | 0 | 6 | 0 | $0.5332 |

## Per case

| Case | sonnet |
|---|---|
| T01 | pass |
| T02 | pass |
| T03 | pass |
| T04 | pass |
| T05 | pass |
| T06 | pass |
| T07 | pass |
| T08 | pass |
| T09 | pass |
| T10 | pass |

## Deck quality

Graded 1-5 per rubric criterion; quality is the mean. Fit is over tasks with a request.

| Variant | Legal deck | Quality | Named commander used | plan | fit | mana | ramp | draw | interaction |
|---|---|---|---|---|---|---|---|---|---|
| sonnet | 100% | 3.07 | 100% | 2.60 | 2.71 | 3.10 | 2.80 | 3.60 | 3.40 |

## Findings

Written by hand after reading the run (`evals/runs/deck_tasks/2026-10-07T173152-sonnet.json`) and the grader's reasons. This is the deck-build baseline (#122).

**Every task ends in a legal deck.** All 10 runs finished with a legal proposal. 6 proposals were rejected along the way, all fixed in the same run, and no tool calls failed. Both named commanders were used. A run took 4.8 turns, about a minute, and $0.105 on average.

**Quality follows the pool more than the request.**

| Pool | Tasks | Quality |
|---|---|---|
| The owner's TCGplayer collection | T06, T07, T08 | **4.17-4.50** |
| `pool_300_test` | T04, T05 | 3.00-3.40 |
| `pool_300` | T01, T02, T03, T09, T10 | **2.17-2.40** |

- **The random 300-card pools are thin:** a few playable cards per colour. The grader's complaints there are what a thin pool forces: 43-45 lands, "off-plan filler" (Tsabo's Web, Dawn-Blessed Pennant, Righteous Avengers) and little removal.
- **The owner's real collection** gave focused decks: Sheoldred aristocrats, a Gisa and Geralf Zombie deck, and Kangee flyers (fit 5).
- The rubric tells the grader not to penalise what the pool lacks, but the grader can't see the pool, so it can't tell "the pool had nothing better" from "the agent chose badly". **A deck's score is therefore partly a score of its pool.** Variant comparisons (#125) stay fair, because every variant gets the same pools.

**Where the agent itself is weaker:**
- **Fit and plan on thin pools (2-2.7):** for the artifacts theme (T03), about 25 of 54 nonland cards are artifacts, but few reward artifacts. For Pia and Kiran (T02), the sacrifice ability is barely used.
- **Refines change too little.** "Add more removal and cut the weakest cards" (T09) was one proposal that left the weakest cards in. "Lower the curve" (T10) still kept a 7-drop and several 6-drops. Both scored fit 2. The pool's lack of removal explains part of T09, but not the uncut filler.
- **Mana bases on thin pools run 43-45 lands,** because the agent fills the deck with basics when it runs out of playable cards. That's legal, but the grader scores it 2.

**The run itself.**
- **The first attempt lost every grade:** the grade's schema made the scores an open dict, which structured output allows only as `{}`. That was fixed with explicit fields (`60e5fc9`) and cost $0.34.
- **T08-T10 hit a shared cost cap** in the first full run: one model client was used for the whole suite. That was fixed with services per case (`365713e`), and the three tasks were run again and merged into this run.
- **Cost:** the agent $1.05 and grading (Opus 5.5) $0.53 for this run. With the failed attempts, the deck baseline cost $1.95.
