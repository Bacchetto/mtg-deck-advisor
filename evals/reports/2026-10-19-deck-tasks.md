# Deck tasks, 2026-10-19 20:36 UTC

Tasks: `evals/datasets/deck_tasks.jsonl` (26). Quality graded by claude-opus-5-5 with [the rubric](../rubrics/deck_quality.md); legality is checked by code.

## Variants

| Variant | Model | Prompt | Card search | Rules search | Rerank | Commit |
|---|---|---|---|---|---|---|
| sonnet | claude-sonnet-5-5 | default | hybrid | vector | yes | 180349c |

## Results

| Variant | Cases | Success | Total cost | Mean cost | Median latency | Mean turns | Tool errors | Rejected proposals | Skipped | Grading cost |
|---|---|---|---|---|---|---|---|---|---|---|
| sonnet | 8 | 100% | $0.9799 | $0.1225 | 65.9 s | 4.8 | 0 | 4 | 0 | $0.4271 |

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

## Deck quality

Graded 1-5 per rubric criterion; quality is the mean. Fit is over tasks with a request.

| Variant | Legal deck | Quality | Named commander used | plan | fit | mana | ramp | draw | interaction |
|---|---|---|---|---|---|---|---|---|---|
| sonnet | 100% | 3.48 | 100% | 2.88 | 3.40 | 3.75 | 3.25 | 4.00 | 3.62 |
