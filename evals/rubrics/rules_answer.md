# Rubric: is a rules answer correct?

Used to grade the agent's answers to the rules Q&A set (`evals/datasets/rules_qa.jsonl`, #121). A model grades each answer with this rubric, and the owner grades a sample by hand with the same rubric, so the two can be compared (EVL-3).

**What's graded is correctness only.** Citations are checked separately, by code, against each question's labelled rules. Style, length and tone don't count.

## What the grader is given

- **The question.**
- **The key facts:** the statements a correct answer must make, written from the rule text.
- **The answer** to grade.

## Verdicts

- **correct:** the answer states every key fact, in any wording, and says nothing that contradicts the rules. Extra true detail is fine.
- **partly correct:** the answer states at least one key fact but misses another, or includes a minor inaccuracy that wouldn't change how the game is played.
- **incorrect:** the answer misses every key fact, or states something that contradicts a key fact or the rules in a way that would change how the game is played. A "yes" where the answer is "no" is incorrect, whatever else it says.

## How to judge

1. **A fact counts as stated** when the answer says it plainly or unmistakably implies it. "You can't run two Sol Rings" states "one copy of each card".
2. **Contradiction outweighs coverage.** An answer that states every key fact and also gets one wrong is at best partly correct.
3. **Don't reward hedging.** "It might be 40 life" doesn't state "40 life".
4. **Judge against the key facts, not your own memory of the rules.** If the key facts seem wrong, still grade against them, and say so in the reason.

## Output

- **verdict:** `correct`, `partly correct` or `incorrect`.
- **missing_facts:** the key facts not stated, word for word.
- **contradictions:** what the answer gets wrong, briefly.
- **reason:** one or two sentences.

## Scoring

- **Per answer:** correct = 1, partly correct = 0.5, incorrect = 0.
- **Agreement with the owner:** the share of answers where the two verdicts are the same.
