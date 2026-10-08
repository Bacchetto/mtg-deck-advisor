# Deck tasks, 2026-10-08 22:02 UTC

Tasks: `evals/datasets/deck_tasks.jsonl` (14). Quality graded by claude-opus-5-5 with [the rubric](../rubrics/deck_quality.md); legality is checked by code.

## Variants

| Variant | Model | Prompt | Card search | Rules search | Rerank | Commit |
|---|---|---|---|---|---|---|
| sonnet | claude-sonnet-5-5 | default | hybrid | vector | yes | 7ec9d85 |

## Results

| Variant | Cases | Success | Total cost | Mean cost | Median latency | Mean turns | Tool errors | Rejected proposals | Skipped | Grading cost |
|---|---|---|---|---|---|---|---|---|---|---|
| sonnet | 6 | 100% | $0.3196 | $0.0533 | 42.1 s | 5.0 | 0 | 4 | 0 | $0.3369 |

## Per case

| Case | sonnet |
|---|---|
| T09 | pass |
| T10 | pass |
| T11 | pass |
| T12 | pass |
| T13 | pass |
| T14 | pass |

## Deck quality

Graded 1-5 per rubric criterion; quality is the mean. Fit is over tasks with a request.

| Variant | Legal deck | Quality | Named commander used | plan | fit | mana | ramp | draw | interaction |
|---|---|---|---|---|---|---|---|---|---|
| sonnet | 100% | 3.39 | 0% | 3.17 | 3.33 | 3.17 | 3.33 | 4.00 | 3.33 |
