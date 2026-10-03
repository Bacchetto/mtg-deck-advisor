# Card summaries written offline (#74), 2026-10-03

**The idea:** the local chat model writes one plain-English sentence per card ("Destroys all creatures for less cost if life is low. Used for board wipe and late-game control"). The sentence is embedded and used in search, to bridge players' words and card templating with no LLM at search time.

All results are on the **dev** set, compared with the best configuration from #73 (`embed-8b`: `qwen3-embedding:8b`, hybrid with the keyword arm at 0.5). Script: `scripts/retrieval_experiments.py`. There's one report per comparison in this folder.

## Generation

| | |
|---|---|
| Model | `qwen3:14b`, temperature 0, batches of 25, structured output validated by `ModelClient` |
| Prompt | one sentence, in the words a player would search with; don't repeat the card's name or its rules wording (`summaries-v1`) |
| Cards | all 32,116 Commander-legal cards |
| Time | about 0.74 s per card, so about 6.6 h of GPU time in all |
| Failures | one batch of 25 came back with 24 answers, the same way every time at temperature 0 (the model skipped one of two vanilla cards). Those 25 were retried one by one, and all succeeded. |

**The run crashed the PC twice.** Both were hard hangs (Kernel-Power 41, no bugcheck) after 30 to 65 min at full GPU load. Generation speed sagged from 56 to 38 tokens/s before the second crash, which points to thermal or power throttling. The machine also had unexplained crashes before Ollama was installed. After the owner lowered the GPU power limit by 10%, the run held about 55 tokens/s for the remaining 5.5 h with no crash. Work committed per batch, so no summaries were lost.

**The summary embedding hit a Windows network-port limit** ("insufficient buffer space"), the same error as the first 8b run in #73. Ollama makes an internal HTTP call per text, and a long run exhausts Windows' short-lived ports. The script now waits 90 s and retries. Production embedding with the 8b model will need the same (#76).

## Results

| Variant | Cards: recall@10 / MRR@10 | Change | W / L | Verdict |
|---|---|---|---|---|
| `embed-8b` (best from #73) | 0.72 / 0.63 | | | |
| Summary appended to the card text (pilot, pool queries only) | pool 0.67 → 0.66 | −1.0 | 5 / 7 | no |
| Summary as its own vector, for **every** query | 0.75 / 0.65 | +2.4 [−5.0, +9.1] | 16 / 10 | **no** (below +3) |
| Summary as its own vector, for **pool** queries only | **0.78** / 0.64 | **+5.3 [+1.8, +9.4]** | 10 / 3 | **adopt** |

"Summary as its own vector" means three-way reciprocal rank fusion: the card vector, the summary vector and the keyword arm, with weights 1, 1 and 0.5. Rules are unaffected (1.00 / 0.76).

**Why "everywhere" loses and "pool only" wins:** the summaries describe what a card *does* and leave out its name.
- **Deck-building needs over a pool** gain a lot: recall@10 0.67 → 0.78, MRR 0.70 → 0.73.
- **Catalogue searches lose.** Recall@10 goes 0.77 → 0.72, and name searches' MRR drops from 1.00 to 0.31, because the summary vector pulls rankings toward cards that *do* similar things.

So summaries are used only for searches within a pool, which is how the product searches when building a deck. A catalogue search (by name, mechanic or description) stays as it was.

**Latency:** median 149 ms per card search (p90 438 ms), within the 2 s budget.

## Caveats

- **The pool-only routing was decided before this run, for fairness:** in the pilot only pool cards had summaries. It wasn't designed to win, but it's still the variant chosen after seeing "everywhere" lose. That's a decision made on dev, and the held-out test set (#76) is the honest check.
- **Summaries can hallucinate where a card gives the model nothing.** Goblin Cavaliers is a vanilla creature, but its summary claims haste and "attacks if able". Summaries are only a retrieval signal: the agent reads the card's real rules text, and the deck validator never uses summaries.
- **Pool queries are 21 of the 44**, so the interval is wide.
- **Version change mid-run:** the first 2,650 summaries were written by Ollama 0.35.0, the rest by 0.35.1 (auto-updated after the first crash). With temperature 0 and the same model weights, any effect should be negligible.

## What adopting it would mean (decided in #76)

- **Storage:** a `card_summaries` table (summary, content hash, prompt version, model), like `card_roles`, and a summary vector per card.
- **Generation:** about 6.6 h of local GPU time for the whole catalogue, then only new or changed cards at ingestion (about 0.75 s each). Alternatively, summarise on demand for submitted pools, as role tagging does.
- **Search:** three-way fusion for pool-scoped card searches.
