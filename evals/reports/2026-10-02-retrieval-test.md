# Retrieval eval (test set), 2026-10-02

Eval set: **test**, `retrieval_cards_test.csv` (41 card queries) and `retrieval_rules_test.csv` (22 rules questions), owner-reviewed. Embedding model: `qwen3-embedding:0.6b`. Script: `scripts/evaluate_retrieval.py`. Each search returns its top 10; MRR counts 0 when nothing relevant is in them.

## Results by mode

**vector**

| Set | queries | recall@5 | recall@10 | MRR@10 |
|---|---|---|---|---|
| Cards: catalogue | 19 | 0.43 | 0.49 | 0.40 |
| Cards: in pool | 22 | 0.42 | 0.54 | 0.67 |
| Cards: all | 41 | 0.43 | 0.52 | 0.55 |
| Rules | 22 | 0.86 | 1.00 | 0.67 |

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
| Cards: catalogue | 19 | 0.55 | 0.71 | 0.49 |
| Cards: in pool | 22 | 0.40 | 0.56 | 0.58 |
| Cards: all | 41 | 0.47 | 0.63 | 0.54 |
| Rules | 22 | 0.57 | 0.59 | 0.44 |

## Side by side (recall@10 / MRR@10)

| Set | vector | keyword | hybrid |
|---|---|---|---|
| Cards: catalogue (19) | 0.49 / 0.40 | 0.53 / 0.38 | 0.71 / 0.49 |
| Cards: in pool (22) | 0.54 / 0.67 | 0.46 / 0.45 | 0.56 / 0.58 |
| Cards: all (41) | 0.52 / 0.55 | 0.49 / 0.42 | 0.63 / 0.54 |
| Rules (22) | 1.00 / 0.67 | 0.41 / 0.12 | 0.59 / 0.44 |
| Cards, kind `name` (4) | 1.00 / 0.79 | 1.00 / 0.69 | 1.00 / 0.80 |
| Cards, kind `need` (22) | 0.54 / 0.67 | 0.46 / 0.45 | 0.56 / 0.58 |
| Cards, kind `paraphrase` (12) | 0.42 / 0.27 | 0.25 / 0.16 | 0.58 / 0.29 |
| Cards, kind `term` (3) | 0.13 / 0.40 | 1.00 / 0.83 | 0.80 / 0.83 |
| Rules, kind `commander` (2) | 1.00 / 1.00 | 1.00 / 0.14 | 1.00 / 0.75 |
| Rules, kind `general` (6) | 1.00 / 0.34 | 0.17 / 0.08 | 0.17 / 0.08 |
| Rules, kind `keyword` (14) | 1.00 / 0.76 | 0.43 / 0.13 | 0.71 / 0.54 |

## Latency

Median per search, including embedding the query with the local model.

| | vector | keyword | hybrid |
|---|---|---|---|
| Cards (ms) | 22 | 5 | 24 |
| Rules (ms) | 18 | 6 | 36 |

## Embedding text variants

Rules: the rule's own text (as embedded), against the same text with its section and its parent's first sentence (ADR 0010's first design).

| Set | queries | recall@5 | recall@10 | MRR@10 |
|---|---|---|---|---|
| Rules, text alone: vector | 22 | 0.86 | 1.00 | 0.67 |
| Rules, with context: vector | 22 | 0.77 | 0.95 | 0.59 |
| Rules, text alone: hybrid | 22 | 0.57 | 0.59 | 0.44 |
| Rules, with context: hybrid | 22 | 0.57 | 0.59 | 0.34 |

## Per query (recall@10)

Misses are the relevant items hybrid search didn't return in its top 10.

| Query | vector | keyword | hybrid | missed by hybrid |
|---|---|---|---|---|
| TC01 a one-mana green elf that taps for green mana | 0.00 | 0.00 | 0.00 | Elvish Mystic; Fyndhorn Elves; Llanowar Elves |
| TC02 an instant that counters a spell unless its controller pays three | 0.00 | 0.00 | 0.00 | Geistlight Snare; Mana Leak; Metallic Rebuke; Mystical Dispute |
| TC03 a land that exiles a player's graveyard when it enters | 0.00 | 1.00 | 1.00 |  |
| TC04 give every permanent I control hexproof and indestructible until end of turn | 0.50 | 0.50 | 1.00 |  |
| TC05 until my next turn my life total can't change and my permanents phase out | 1.00 | 1.00 | 1.00 |  |
| TC06 win the game instead of losing when drawing from an empty library | 0.00 | 0.50 | 0.50 | Laboratory Maniac |
| TC07 an enchantment that draws an extra card every upkeep at the cost of one life | 1.00 | 0.00 | 1.00 |  |
| TC08 bring back a creature from any graveyard, losing life equal to its mana value | 1.00 | 0.00 | 1.00 |  |
| TC09 pay X life to give every creature -X/-X | 0.00 | 0.00 | 0.00 | Toxic Deluge |
| TC10 an artifact that makes enter-the-battlefield triggers happen twice | 1.00 | 0.00 | 1.00 |  |
| TC11 each opponent loses X life and I gain that much | 0.50 | 0.00 | 0.50 | Stensian Sanguinist // Exsanguinate |
| TC12 a two-mana artifact that taps for colorless and can be sacrificed to draw a card | 0.00 | 0.00 | 0.00 | Mind Stone |
| TC13 recover | 0.29 | 1.00 | 0.86 | Garza's Assassin |
| TC14 dethrone | 0.11 | 1.00 | 0.56 | Marchesa's Emissary; Marchesa's Infiltrator; Marchesa's Smuggler; Scourge of the Throne |
| TC15 transfigure | 0.00 | 1.00 | 1.00 |  |
| TC16 the returned king | 1.00 | 1.00 | 1.00 |  |
| TC17 ur dragon | 1.00 | 1.00 | 1.00 |  |
| TC18 yuriko tigers shadow | 1.00 | 1.00 | 1.00 |  |
| TC19 thassas oracle | 1.00 | 1.00 | 1.00 |  |
| TP01 counter a spell or ability | 1.00 | 1.00 | 1.00 |  |
| TP02 sweepers that hit every creature | 0.00 | 0.00 | 0.00 | Burn Down the House; Catastrophe |
| TP03 exile an opponent's creature | 0.75 | 0.00 | 0.25 | Bring to Trial; Celebrate the Mountain-king; Expel |
| TP04 burn that can kill a creature | 0.57 | 0.14 | 0.14 | Combustion Technique; Jaya's Immolating Inferno; Knollspine Invocation; Pyromania; Shower of Sparks; Shredded Sails |
| TP05 answers to an artifact or enchantment | 0.14 | 0.14 | 0.29 | Atraxa's Fall; Conclave Tribunal; Invoke the Divine; Rust Scarab; Shredded Sails |
| TP06 draw cards over and over | 0.10 | 0.00 | 0.10 | Ant-Man, Reformed Rogue; Elrond, Moon-Reader; Enchantress's Presence; Robot Domination; Senator Peacock; Soulknife Spy; The Mighty Thor, Jane Foster; Third Path Savant; Thrasios, Triton Hero |
| TP07 creatures that tap for mana | 0.00 | 0.25 | 0.25 | Great Divide Guide; Omen Hawker; Runadi, Behemoth Caller |
| TP08 mana rocks | 1.00 | 0.50 | 1.00 |  |
| TP09 make creature tokens | 0.62 | 0.38 | 0.38 | Ayula's Influence; Lys Alana Huntmaster; March of the World Ooze; Saproling Cluster; Spore Burst |
| TP10 graveyard hate | 0.50 | 1.00 | 0.50 | Arashin Sunshield |
| TP11 make opponents discard | 0.50 | 0.38 | 0.62 | Herald of Anguish; Liliana Vess; Whispering Madness |
| TP12 drain opponents' life | 0.00 | 0.80 | 0.60 | Curse of Fool's Wisdom; Syphon Soul |
| TP13 search my library for a creature | 0.75 | 0.50 | 0.50 | Bring to Light; Shared Summons |
| TP14 put a creature from my graveyard onto the battlefield | 0.25 | 0.25 | 0.00 | Liliana Vess; Tyvar, Jubilant Brawler; Underworld Sentinel; Unearth |
| TP15 protect my other creatures from removal | 0.25 | 0.25 | 0.50 | Foot Elite; Umbra Mystic |
| TP16 return a creature to its owner's hand | 0.67 | 0.33 | 0.83 | Geistwave |
| TP17 tap an opponent's creature | 0.20 | 0.20 | 0.40 | Arashin Sunshield; Glaring Aegis; Janjeet Sentry |
| TP18 copy instants and sorceries | 1.00 | 1.00 | 1.00 |  |
| TP19 dual lands | 1.00 | 1.00 | 1.00 |  |
| TP20 take control of an opponent's permanent | 0.50 | 0.00 | 1.00 |  |
| TP21 no maximum hand size | 1.00 | 1.00 | 1.00 |  |
| TP22 generate Treasure | 1.00 | 1.00 | 1.00 |  |
| TR01 Does a creature with vigilance tap when it attacks? | 1.00 | 0.00 | 0.00 | 702.20b |
| TR02 What can block a creature with flying? | 1.00 | 0.00 | 1.00 |  |
| TR03 How many creatures does it take to block a creature with menace? | 1.00 | 0.00 | 0.00 | 702.111b |
| TR04 How does first strike change combat damage? | 1.00 | 1.00 | 1.00 |  |
| TR05 Can a creature with haste attack the turn it comes into play? | 1.00 | 0.00 | 0.00 | 702.10b |
| TR06 What happens when a creature with persist dies? | 1.00 | 0.00 | 1.00 |  |
| TR07 How does convoke let my creatures pay for a spell? | 1.00 | 0.00 | 1.00 |  |
| TR08 What does cumulative upkeep cost me each turn? | 1.00 | 1.00 | 1.00 |  |
| TR09 What does it mean to scry? | 1.00 | 1.00 | 1.00 |  |
| TR10 What does surveil do? | 1.00 | 1.00 | 1.00 |  |
| TR11 How does a fight between two creatures work? | 1.00 | 0.00 | 1.00 |  |
| TR12 Can anyone respond to a spell with split second? | 1.00 | 1.00 | 1.00 |  |
| TR13 How does cycling work? | 1.00 | 1.00 | 1.00 |  |
| TR14 What can't happen to a creature with protection from a color? | 1.00 | 0.00 | 0.00 | 702.16b |
| TR15 When is a creature destroyed for having too much damage? | 1.00 | 0.00 | 0.00 | 704.5g |
| TR16 What happens to a planeswalker with no loyalty left? | 1.00 | 0.00 | 0.00 | 704.5i |
| TR17 How many poison counters make a player lose? | 1.00 | 0.00 | 0.00 | 704.5c |
| TR18 When does damage wear off creatures? | 1.00 | 0.00 | 0.00 | 514.2 |
| TR19 When can I cast a sorcery? | 1.00 | 1.00 | 1.00 |  |
| TR20 Does the first player draw a card on their first turn? | 1.00 | 0.00 | 0.00 | 103.8a |
| TR21 Is Commander played as a free-for-all by default? | 1.00 | 1.00 | 1.00 |  |
| TR22 Can I use cards from outside the game in Commander? | 1.00 | 1.00 | 1.00 |  |

## Caveats

- **Small set.** 44 card queries and 45 rules questions: one query moves a set's recall by about 2 points, and a kind with 2 queries by 50. Differences of a few points are noise.
- **Labels.** Drafted by Claude from checkable sources and reviewed by the owner, who made no corrections. Catalogue queries list known targets, not every relevant card in 32,000, so their recall can understate a search that finds other good answers. Pool queries were judged against all 300 pool cards.
- **Held out.** This set shares no pool cards, labelled cards, answering rules or queries with the dev set, and avoids the cards the embedding model was chosen on. It is scored only for the baseline and the final configuration.
- **Variant search path.** Variants are embedded into a temporary table and searched exactly; the shipped configuration goes through the app's real search path. If anything, that favours the variants.
- **Selection on the test set.** The defaults below were chosen on this same set, so the shipped configuration's numbers are somewhat optimistic. There's no held-out set yet; any further tuning (fusion weights, keyword query form) needs one first.

## Findings

_Written by hand. This is the **baseline** run of the held-out set, for the configuration shipped in PR #70 (cards hybrid, rules vector). The test set is scored once more, for the final configuration after the improvement experiments (#72–#76). No decision is taken from these numbers._

1. **The held-out set agrees with the defaults chosen on dev.**
   - **Cards: hybrid is ahead by a wider margin than on dev.** Recall@10 is 0.63 against 0.52 for vector (dev: 0.58 against 0.53), with the same MRR (0.54 against 0.55).
   - **Rules: vector is clearly better.** Recall@10 is 1.00 against 0.59 for hybrid.
   - **The parent context loses again** (0.95 against 1.00 for the rule's text alone), confirming the ADR 0010 revision.
2. **The baseline to beat** is cards, hybrid: recall@10 **0.63**, MRR **0.54**, and rules, vector: recall@10 **1.00**, MRR **0.67**.
3. **The same weak spots as dev.**
   - **Paraphrases:** recall@10 0.58, MRR 0.29.
   - **Needs over a pool:** recall@10 0.56.
   - **Rules ranking:** every answer is in the top 10, but often low. General questions have an MRR of 0.34.
   - **Mechanic terms favour keyword search:** recall@10 is 1.00 with keyword against 0.13 with vector, and hybrid keeps most of that (0.80).
4. **Latency is lower than on dev.** The medians are 24 ms for a hybrid card search and 18 ms for a rules search, against about 86 ms on dev. Much of the dev figure was the first query after the model loaded, and this run was warmer. The experiments will report medians and 90th percentiles from a warm model.
