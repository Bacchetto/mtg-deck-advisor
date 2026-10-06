# Eval datasets

Labelled data for the evals. Each set is drafted by Claude from checkable sources and reviewed by the project owner. The draft and the owner's corrections are separate commits, so the history shows where a human disagreed.

| File | What it labels | Used by |
|---|---|---|
| `role_tagging_gold.csv` | The roles of 100 cards (ramp, removal...) | `scripts/compare_role_taggers.py` |
| `retrieval_cards.csv` | Card search queries and the cards that answer them | the retrieval eval (Milestone 4) |
| `retrieval_rules.csv` | Rules questions and the rule numbers that answer them | the retrieval eval (Milestone 4) |
| `pool_300.txt` | A fixed pool of 300 cards for the in-pool card queries | the retrieval eval |
| `retrieval_cards_test.csv`, `retrieval_rules_test.csv`, `pool_300_test.txt` | The same, held out | the retrieval eval's baseline and final runs only |
| `pool_tcgplayer_collection.csv` | A real collection: 524 cards (447 different), exported from the TCGplayer app | TCGplayer CSV import; evaluation pool for Milestone 7 |
| `validator_decks.jsonl` | 12 decks, each with the Commander violations it must get | the eval gate (`evaluation/gate.py`) |

## Retrieval sets

There are two sets with the same format:

- **dev** (`retrieval_cards.csv`, `retrieval_rules.csv`, `pool_300.txt`): written before any search code. Retrieval is tuned on this set, so its scores for the chosen configuration are somewhat optimistic.
- **test** (the `_test` files): written before any tuning, then frozen. It's scored twice, once for the baseline and once for the final configuration, so the reported gain is honest.
  - Query IDs start with `T`.
  - It shares no pool cards, labelled cards, answering rules or query wording with dev.
  - It avoids the 20 cards the embedding model was chosen on (ADR 0009).

Check a set against the database with:

```bash
python -m mtg_deck_advisor.evaluation.retrieval_set --set dev    # or --set test
```

For the test set, this also checks that it's held out from dev.

This checks that every card exists and is Commander-legal, that pool queries only label pool cards, that relevant cards pass the query's own filters, and that every rule number exists.

**`retrieval_cards.csv` columns:**
- `id`: `C..` for catalogue queries, `P..` for pool queries.
- `kind`, one of:
  - `paraphrase`: a description in different words from the card
  - `term`: an exact mechanic name
  - `name`: a card name, possibly mistyped
  - `need`: a deck-building need
- `scope`: `catalogue` searches every Commander-legal card; `pool` searches only `pool_300.txt`.
- `query`: what's searched for.
- `filters`: the exact filters the query runs with, such as `identity=G; type=Artifact; mv_min=1; mv_max=3`. Use `identity=C` for colorless.
- `relevant`: the cards that answer the query, separated by `;`. Use full names for two-faced cards (`Bellowing Bruiser // Beat a Path`).
- `notes`: why borderline cards were included or left out.

**Which cards count as relevant:**
- **Catalogue queries** have a few known targets each. Where a query describes an exact effect, an SQL search of the oracle text found every card with it.
- **Pool queries** were judged against **every** card in the pool, so their relevant lists are complete and recall is exact.

**`retrieval_rules.csv` columns:**
- `id`
- `kind`: `commander`, `keyword` or `general`
- `question`
- `relevant`: the rule numbers that answer the question, such as `903.5a; 704.6c`
- `notes`

**`pool_300.txt`:** a random sample of 300 Commander-legal cards. It was drawn once (Postgres `setseed(0.52)`, then `ORDER BY random() LIMIT 300`) and is committed so it never changes.

**`pool_300_test.txt`:** drawn the same way with `setseed(0.71)`, excluding the dev pool. One drawn card, Surging Flame, was already labelled in the dev set. It was swapped for one more random draw (`setseed(0.72)`, Michelangelo, the Heart), which was judged against every pool query and answers none of them.


**`pool_tcgplayer_collection.csv`:** the project owner's own collection, exported from the TCGplayer app on 2026-10-05, unedited. Unlike the random pools it's a real player's cards: whole precons, several copies of some cards, and the TCGplayer format's quirks (names in "Product Name", counts in "Add to Quantity", variant tags such as "Sol Ring (C18)" and "(Showcase)", foil and normal rows for the same card). All 462 rows resolve: 524 cards, 447 different.


**`validator_decks.jsonl`:** built from the recorded Adeline draft (`pool_300`, the no-key demo's deck), legal as it is, and changed one way per case: a card short or over, a second copy, an off-color card, a card the pool lacks, a banned card (Mana Crypt), an Un-card (Adorable Kitten), a non-legendary commander, and two problems at once. Each case's `expected` lists exactly the violation codes the validator must report; `pool` is the pool file it's checked against, or null for the deck's own cards (the format's rules only).
