# Retrieval eval (test set), 2026-10-04

Eval set: **test**, `retrieval_cards_test.csv` (41 card queries) and `retrieval_rules_test.csv` (22 rules questions), owner-reviewed. Embedding model: `qwen3-embedding:8b`. Script: `scripts/evaluate_retrieval.py`. Each search returns its top 10; MRR counts 0 when nothing relevant is in them. **shipped** is what the app does: cards hybrid with the reranker (RERANK_MODEL), rules vector.

## Results by mode

**vector**

| Set | queries | recall@5 | recall@10 | MRR@10 |
|---|---|---|---|---|
| Cards: catalogue | 19 | 0.56 | 0.57 | 0.62 |
| Cards: in pool | 22 | 0.53 | 0.68 | 0.69 |
| Cards: all | 41 | 0.54 | 0.63 | 0.66 |
| Rules | 22 | 0.95 | 1.00 | 0.75 |

**keyword**

| Set | queries | recall@5 | recall@10 | MRR@10 |
|---|---|---|---|---|
| Cards: catalogue | 19 | 0.42 | 0.53 | 0.38 |
| Cards: in pool | 22 | 0.36 | 0.46 | 0.45 |
| Cards: all | 41 | 0.39 | 0.49 | 0.42 |
| Rules | 22 | 0.32 | 0.41 | 0.12 |

**hybrid**

| Set | queries | recall@5 | recall@10 | MRR@10 |
|---|---|---|---|---|
| Cards: catalogue | 19 | 0.59 | 0.64 | 0.55 |
| Cards: in pool | 22 | 0.55 | 0.71 | 0.83 |
| Cards: all | 41 | 0.57 | 0.68 | 0.70 |
| Rules | 22 | 0.57 | 0.59 | 0.46 |

**shipped**

| Set | queries | recall@5 | recall@10 | MRR@10 |
|---|---|---|---|---|
| Cards: catalogue | 19 | 0.66 | 0.67 | 0.69 |
| Cards: in pool | 22 | 0.64 | 0.76 | 0.88 |
| Cards: all | 41 | 0.65 | 0.72 | 0.79 |
| Rules | 22 | 0.95 | 1.00 | 0.75 |

## Side by side (recall@10 / MRR@10)

| Set | vector | keyword | hybrid | shipped |
|---|---|---|---|---|
| Cards: catalogue (19) | 0.57 / 0.62 | 0.53 / 0.38 | 0.64 / 0.55 | 0.67 / 0.69 |
| Cards: in pool (22) | 0.68 / 0.69 | 0.46 / 0.45 | 0.71 / 0.83 | 0.76 / 0.88 |
| Cards: all (41) | 0.63 / 0.66 | 0.49 / 0.42 | 0.68 / 0.70 | 0.72 / 0.79 |
| Rules (22) | 1.00 / 0.75 | 0.41 / 0.12 | 0.59 / 0.46 | 1.00 / 0.75 |
| Cards, kind `name` (4) | 1.00 / 1.00 | 1.00 / 0.69 | 1.00 / 1.00 | 1.00 / 1.00 |
| Cards, kind `need` (22) | 0.68 / 0.69 | 0.46 / 0.45 | 0.71 / 0.83 | 0.76 / 0.88 |
| Cards, kind `paraphrase` (12) | 0.51 / 0.56 | 0.25 / 0.16 | 0.58 / 0.41 | 0.66 / 0.67 |
| Cards, kind `term` (3) | 0.22 / 0.33 | 1.00 / 0.83 | 0.43 / 0.50 | 0.30 / 0.33 |
| Rules, kind `commander` (2) | 1.00 / 1.00 | 1.00 / 0.14 | 1.00 / 1.00 | 1.00 / 1.00 |
| Rules, kind `general` (6) | 1.00 / 0.44 | 0.17 / 0.08 | 0.17 / 0.17 | 1.00 / 0.44 |
| Rules, kind `keyword` (14) | 1.00 / 0.85 | 0.43 / 0.13 | 0.71 / 0.51 | 1.00 / 0.85 |

## Latency

Median per search, including embedding the query with the local model.

| | vector | keyword | hybrid | shipped |
|---|---|---|---|---|
| Cards (ms) | 51 | 4 | 50 | 1130 |
| Rules (ms) | 35 | 6 | 38 | 27 |

## Embedding text variants

Rules: the rule's own text (as embedded), against the same text with its section and its parent's first sentence (ADR 0010's first design).

| Set | queries | recall@5 | recall@10 | MRR@10 |
|---|---|---|---|---|
| Rules, text alone: vector | 22 | 0.95 | 1.00 | 0.75 |
| Rules, with context: vector | 22 | 0.95 | 0.95 | 0.69 |
| Rules, text alone: hybrid | 22 | 0.57 | 0.59 | 0.46 |
| Rules, with context: hybrid | 22 | 0.57 | 0.57 | 0.36 |

## Per query (recall@10)

Misses are the relevant items the shipped configuration didn't return in its top 10.

| Query | vector | keyword | hybrid | shipped | missed by shipped |
|---|---|---|---|---|---|
| TC01 a one-mana green elf that taps for green mana | 0.67 | 0.00 | 0.67 | 0.67 | Fyndhorn Elves |
| TC02 an instant that counters a spell unless its controller pays three | 0.00 | 0.00 | 0.25 | 0.25 | Geistlight Snare; Metallic Rebuke; Mystical Dispute |
| TC03 a land that exiles a player's graveyard when it enters | 0.00 | 1.00 | 0.00 | 0.00 | Bojuka Bog |
| TC04 give every permanent I control hexproof and indestructible until end of turn | 0.50 | 0.50 | 0.50 | 0.50 | Restoration Magic |
| TC05 until my next turn my life total can't change and my permanents phase out | 1.00 | 1.00 | 1.00 | 1.00 |  |
| TC06 win the game instead of losing when drawing from an empty library | 0.50 | 0.50 | 1.00 | 1.00 |  |
| TC07 an enchantment that draws an extra card every upkeep at the cost of one life | 1.00 | 0.00 | 1.00 | 1.00 |  |
| TC08 bring back a creature from any graveyard, losing life equal to its mana value | 1.00 | 0.00 | 1.00 | 1.00 |  |
| TC09 pay X life to give every creature -X/-X | 1.00 | 0.00 | 1.00 | 1.00 |  |
| TC10 an artifact that makes enter-the-battlefield triggers happen twice | 0.00 | 0.00 | 0.00 | 0.00 | Panharmonicon |
| TC11 each opponent loses X life and I gain that much | 0.50 | 0.00 | 0.50 | 0.50 | Stensian Sanguinist // Exsanguinate |
| TC12 a two-mana artifact that taps for colorless and can be sacrificed to draw a card | 0.00 | 0.00 | 0.00 | 1.00 |  |
| TC13 recover | 0.00 | 1.00 | 0.29 | 0.00 | Controvert; Garza's Assassin; Grim Harvest; Icefall; Krovikan Rot; Resize; Sun's Bounty |
| TC14 dethrone | 0.67 | 1.00 | 1.00 | 0.89 | Treasonous Ogre |
| TC15 transfigure | 0.00 | 1.00 | 0.00 | 0.00 | Fleshwrither |
| TC16 the returned king | 1.00 | 1.00 | 1.00 | 1.00 |  |
| TC17 ur dragon | 1.00 | 1.00 | 1.00 | 1.00 |  |
| TC18 yuriko tigers shadow | 1.00 | 1.00 | 1.00 | 1.00 |  |
| TC19 thassas oracle | 1.00 | 1.00 | 1.00 | 1.00 |  |
| TP01 counter a spell or ability | 1.00 | 1.00 | 1.00 | 1.00 |  |
| TP02 sweepers that hit every creature | 0.50 | 0.00 | 0.50 | 0.50 | Burn Down the House |
| TP03 exile an opponent's creature | 0.50 | 0.00 | 0.75 | 1.00 |  |
| TP04 burn that can kill a creature | 0.71 | 0.14 | 0.57 | 0.86 | Jaya's Immolating Inferno |
| TP05 answers to an artifact or enchantment | 0.29 | 0.14 | 0.29 | 0.29 | Celebrate the Mountain-king; Conclave Tribunal; Rust Scarab; Shredded Sails; Urn of Godfire |
| TP06 draw cards over and over | 0.20 | 0.00 | 0.20 | 0.20 | Ant-Man, Reformed Rogue; Elrond, Moon-Reader; Robot Domination; Senator Peacock; Soulknife Spy; The Mighty Thor, Jane Foster; Third Path Savant; Thrasios, Triton Hero |
| TP07 creatures that tap for mana | 0.25 | 0.25 | 0.50 | 0.50 | Omen Hawker; Rosheen, Roaring Prophet |
| TP08 mana rocks | 1.00 | 0.50 | 0.50 | 1.00 |  |
| TP09 make creature tokens | 1.00 | 0.38 | 0.88 | 1.00 |  |
| TP10 graveyard hate | 1.00 | 1.00 | 1.00 | 0.50 | Weathered Runestone |
| TP11 make opponents discard | 0.62 | 0.38 | 0.88 | 0.75 | Ashiok's Adept; Scroll of Griselbrand |
| TP12 drain opponents' life | 0.40 | 0.80 | 0.80 | 0.80 | Gonti's Machinations |
| TP13 search my library for a creature | 0.50 | 0.50 | 0.50 | 0.50 | Bring to Light; General Tazri |
| TP14 put a creature from my graveyard onto the battlefield | 0.50 | 0.25 | 0.75 | 0.75 | Tyvar, Jubilant Brawler |
| TP15 protect my other creatures from removal | 0.50 | 0.25 | 0.50 | 0.50 | Foot Elite; Umbra Mystic |
| TP16 return a creature to its owner's hand | 0.83 | 0.33 | 1.00 | 1.00 |  |
| TP17 tap an opponent's creature | 0.60 | 0.20 | 0.60 | 1.00 |  |
| TP18 copy instants and sorceries | 1.00 | 1.00 | 1.00 | 1.00 |  |
| TP19 dual lands | 1.00 | 1.00 | 1.00 | 1.00 |  |
| TP20 take control of an opponent's permanent | 0.50 | 0.00 | 0.50 | 0.50 | Midnight Crusader Shuttle |
| TP21 no maximum hand size | 1.00 | 1.00 | 1.00 | 1.00 |  |
| TP22 generate Treasure | 1.00 | 1.00 | 1.00 | 1.00 |  |
| TR01 Does a creature with vigilance tap when it attacks? | 1.00 | 0.00 | 0.00 | 1.00 |  |
| TR02 What can block a creature with flying? | 1.00 | 0.00 | 1.00 | 1.00 |  |
| TR03 How many creatures does it take to block a creature with menace? | 1.00 | 0.00 | 0.00 | 1.00 |  |
| TR04 How does first strike change combat damage? | 1.00 | 1.00 | 1.00 | 1.00 |  |
| TR05 Can a creature with haste attack the turn it comes into play? | 1.00 | 0.00 | 0.00 | 1.00 |  |
| TR06 What happens when a creature with persist dies? | 1.00 | 0.00 | 1.00 | 1.00 |  |
| TR07 How does convoke let my creatures pay for a spell? | 1.00 | 0.00 | 1.00 | 1.00 |  |
| TR08 What does cumulative upkeep cost me each turn? | 1.00 | 1.00 | 1.00 | 1.00 |  |
| TR09 What does it mean to scry? | 1.00 | 1.00 | 1.00 | 1.00 |  |
| TR10 What does surveil do? | 1.00 | 1.00 | 1.00 | 1.00 |  |
| TR11 How does a fight between two creatures work? | 1.00 | 0.00 | 1.00 | 1.00 |  |
| TR12 Can anyone respond to a spell with split second? | 1.00 | 1.00 | 1.00 | 1.00 |  |
| TR13 How does cycling work? | 1.00 | 1.00 | 1.00 | 1.00 |  |
| TR14 What can't happen to a creature with protection from a color? | 1.00 | 0.00 | 0.00 | 1.00 |  |
| TR15 When is a creature destroyed for having too much damage? | 1.00 | 0.00 | 0.00 | 1.00 |  |
| TR16 What happens to a planeswalker with no loyalty left? | 1.00 | 0.00 | 0.00 | 1.00 |  |
| TR17 How many poison counters make a player lose? | 1.00 | 0.00 | 0.00 | 1.00 |  |
| TR18 When does damage wear off creatures? | 1.00 | 0.00 | 0.00 | 1.00 |  |
| TR19 When can I cast a sorcery? | 1.00 | 1.00 | 1.00 | 1.00 |  |
| TR20 Does the first player draw a card on their first turn? | 1.00 | 0.00 | 0.00 | 1.00 |  |
| TR21 Is Commander played as a free-for-all by default? | 1.00 | 1.00 | 1.00 | 1.00 |  |
| TR22 Can I use cards from outside the game in Commander? | 1.00 | 1.00 | 1.00 | 1.00 |  |

## Caveats

- **Small set.** 44 card queries and 45 rules questions: one query moves a set's recall by about 2 points, and a kind with 2 queries by 50. Differences of a few points are noise.
- **Labels.** Drafted by Claude from checkable sources and reviewed by the owner, who made no corrections. Catalogue queries list known targets, not every relevant card in 32,000, so their recall can understate a search that finds other good answers. Pool queries were judged against all 300 pool cards.
- **Held out.** This set shares no pool cards, labelled cards, answering rules or queries with the dev set, and avoids the cards the embedding model was chosen on. It is scored only for the baseline and the final configuration.
- **Variant search path.** Variants are embedded into a temporary table and searched exactly; the shipped configuration goes through the app's real search path. If anything, that favours the variants.
- **Selection on the test set.** The defaults below were chosen on this same set, so the shipped configuration's numbers are somewhat optimistic. There's no held-out set yet; any further tuning (fusion weights, keyword query form) needs one first.

## Findings

_Written by hand. This is the **final** run of the held-out set, for the configuration #76 ships:
- `qwen3-embedding:8b` truncated to 1,024 dimensions
- hybrid card search with the keyword arm at half weight
- summary vectors for pool searches
- `qwen3:8b` reranking card searches
- vector search for rules

The set's only other run was the baseline (`2026-10-02-retrieval-test.md`), made before any tuning. No decision was taken from either run._

**Baseline against final, on the held-out set** (both are what the app shipped at the time: cards hybrid, rules vector):

| Held-out | Baseline (#70) | Final (`shipped`) | Change (points) |
|---|---|---|---|
| Cards, all (41): recall@10 / MRR@10 | 0.63 / 0.54 | **0.72 / 0.79** | **+9 / +25** |
| Cards, catalogue (19) | 0.71 / 0.49 | 0.67 / 0.69 | −4 / +20 |
| Cards, in pool (22) | 0.56 / 0.58 | **0.76 / 0.88** | **+20 / +30** |
| Cards, paraphrase (12) | 0.58 / 0.29 | 0.66 / 0.67 | +8 / +38 |
| Cards, mechanic term (3) | 0.80 / 0.83 | **0.30 / 0.33** | **−50 / −50** |
| Rules (22) | 1.00 / 0.67 | 1.00 / 0.75 | 0 / +8 |
| Median card search | 24 ms | 1,130 ms | within the 2 s budget |

1. **The gains hold on queries nobody tuned on.**
   - **Card MRR rose 25 points:** the first right answer moved from around third place to usually first. This is mostly the reranker.
   - **Searches within a pool,** the deck-building case, gained the most: +20 recall@10 and +30 MRR. That's the summaries plus reranking.
   - **Rules MRR rose 8 points** with the 8b embedder.
2. **The dev set was optimistic, as expected.** Cards recall@10 went up 25 points on dev (0.58 → 0.83) and 9 on the held-out set. Choosing among about 20 variants on 44 queries flatters the winners. The held-out number is the one to quote.
3. **One regression: single-word mechanic searches.** "recover", "dethrone" and "transfigure" fell from 0.80 to 0.30 recall@10. Keyword search alone gets all three (1.00).
   - **Why:** both changes that helped elsewhere hurt here. The keyword arm, which finds exact mechanic names, now counts half. And the reranker judges by "what each card does", which a bare mechanic name doesn't describe.
   - **The dev set's three term queries didn't show this** (1.00 throughout), which is exactly the kind of thing a held-out set exists to catch.
   - **The fix belongs to later work,** measured on dev and not on these numbers: let exact keyword-ability matches through, for example by reranking only when the query isn't a single known keyword.
   - It's three queries, so the size of the effect is uncertain, but the direction is clear.
4. **Catalogue recall slipped 4 points** (0.71 → 0.67) while its MRR rose 20. That's within noise for 19 queries, and partly the mechanic-term regression above.
5. **Latency:** a median of 1.1 s per card search, almost all of it reranking, within the 2 s budget. Rules searches stay at about 30 ms. The agent in Milestone 5 will search many times per deck build, so it's about a second per search.
