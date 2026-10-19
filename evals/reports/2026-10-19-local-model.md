# The agent on a local model, compared with Claude, 2026-10-19

Can the agent run on a local model, free, instead of Claude? #152 (MOD-5) added tool calling to the Ollama provider and ran the rules Q&A on `qwen3:14b`, the largest chat model this machine runs. The owner then dropped the deck runs: the GPU time isn't worth it, and the gap was already clear.

## Setup

- **The model:** `qwen3:14b` through Ollama 0.40, on the development machine's GPU, at temperature 0. Agent requests ask for a 16,384-token context (`OLLAMA_AGENT_NUM_CTX`); Ollama's default is too short for an agent turn, and it cuts a longer prompt silently.
- **Two prompts:**
  - **qwen3-14b:** the shipped system prompt, unchanged.
  - **qwen3-14b-nudged:** the shipped prompt plus one line, "Before you answer, always call a tool: never answer from memory alone." (the `tool-first` prompt variant).
- **The same suite as for Claude:** 55 rules questions (45 answerable, 10 not), graded by Sonnet 5.5 against key facts, with citations checked by code.

## Rules Q&A

| 55 questions | Sonnet 5.5 | Haiku 4.5 | qwen3:14b, shipped prompt | qwen3:14b, nudged |
|---|---|---|---|---|
| Searched before answering | 55 | 48 | **14** | 48 |
| Answered, of 45 answerable | 45 | 44 | **13** | 44 |
| Correct (graded) | **99%** | 93% | 23% | 86% |
| Citation precision | 92% | **95%** | 82% | 84% |
| "Not found" when it should | **100%** | 30% | 60% | 80% |
| Success | **84%** | 75% | 18% | 58% |
| Median time per question | 6 s | 7 s | 6 s | 10 s |
| Agent cost, 55 questions | $0.25 | $0.16 | free | free |

Runs: [Sonnet](../runs/rules_qa/2026-10-08T212318-sonnet.json), [Haiku](../runs/rules_qa/2026-10-07T212220-haiku.json), [qwen3:14b](../runs/rules_qa/2026-10-13T151022-qwen3-14b.json), [qwen3:14b nudged](../runs/rules_qa/2026-10-13T152726-qwen3-14b-nudged.json).

- **With the shipped prompt, the local model doesn't use its tools.**
  - **What it did:** on 41 of 55 questions it answered from memory, though the prompt says to search and to answer only from what search returns.
  - **The guardrail held:** it withheld 30 answers for citing rules never retrieved, so wrong answers didn't reach the user.
  - **One of them was wrong:** "a creature with summoning sickness can't block". Even so, an agent that answers 13 of 45 questions isn't usable.
- **One prompt line makes it usable.** Told to call a tool first, it searched on 48 questions, answered 44 of 45, and was 86% correct. That's below Haiku's 93% and Sonnet's 99%, with more partly-correct answers (9) and lower citation precision.
- **It refuses to guess better than Haiku** (80% against 30%), but not as reliably as Sonnet (100%).
- **A model swap isn't a configuration change.** The same prompt and tools that work on Claude need adapting to a local model, and the adaptation needs its own eval.

## Drafting a deck (one run, not graded)

One draft from the owner's collection, on the nudged prompt, timed:

| | Sonnet 5.5 (Milestone 7, 8 drafts) | Haiku 4.5 (Milestone 7, 8 drafts) | qwen3:14b nudged (1 draft) |
|---|---|---|---|
| Finished with a legal deck | 8 of 8 | 8 of 8 | **0 of 1** (hit the 30-turn limit) |
| Turns | 4.8 | 17.1 | 30 |
| Time | 66 s (median) | 212 s (median) | 737 s |
| Proposals rejected | 0.5 per draft | 5.3 per draft | 3, with 66, then 15, then 8 problems |

- **It was converging, slowly.** Its first two proposals mostly invented citations: it quoted rule text instead of citing rule numbers a tool had shown. Its third still had off-color cards and a duplicate.
- **This is one run, so it's indicative only.** The owner dropped the full deck comparison, 8 drafts at about 12 minutes each per prompt.

## Cost
- **Grading the local model's rules answers by Sonnet:** $0.12. The local runs themselves were free.
- **Machine time:** about 45 minutes of GPU for the two rules runs and the draft.

## Takeaways
- **For this agent, the local 14B model is a fallback, not an alternative.** With adaptation it answers rules questions acceptably, but it can't build a deck in the turns Claude needs.
- **The guardrails made a weak model safe, not useful.** Code withheld every ungrounded answer and rejected every illegal deck, so nothing wrong reached the user. But very little right did either.
- **Cost per completed task again beats price per token** ([the Haiku finding](../../docs/findings/haiku-costs-more.md)). A free model that rarely completes the task costs time instead of money.
