# Retrieval eval (dev set), 2026-10-04

Eval set: **dev**, `retrieval_cards.csv` (44 card queries) and `retrieval_rules.csv` (45 rules questions), owner-reviewed. Embedding model: `qwen3-embedding:8b`. Script: `scripts/evaluate_retrieval.py`. Each search returns its top 10; MRR counts 0 when nothing relevant is in them. **shipped** is what the app does: cards hybrid with the reranker (RERANK_MODEL), rules vector.

## Results by mode

**vector**

| Set | queries | recall@5 | recall@10 | MRR@10 |
|---|---|---|---|---|
| Cards: catalogue | 23 | 0.75 | 0.83 | 0.58 |
| Cards: in pool | 21 | 0.53 | 0.67 | 0.72 |
| Cards: all | 44 | 0.65 | 0.75 | 0.65 |
| Rules | 45 | 0.93 | 1.00 | 0.76 |

**keyword**

| Set | queries | recall@5 | recall@10 | MRR@10 |
|---|---|---|---|---|
| Cards: catalogue | 23 | 0.21 | 0.29 | 0.21 |
| Cards: in pool | 21 | 0.29 | 0.39 | 0.35 |
| Cards: all | 44 | 0.25 | 0.33 | 0.28 |
| Rules | 45 | 0.30 | 0.43 | 0.24 |

**hybrid**

| Set | queries | recall@5 | recall@10 | MRR@10 |
|---|---|---|---|---|
| Cards: catalogue | 23 | 0.63 | 0.77 | 0.57 |
| Cards: in pool | 21 | 0.61 | 0.78 | 0.73 |
| Cards: all | 44 | 0.62 | 0.78 | 0.65 |
| Rules | 45 | 0.59 | 0.73 | 0.46 |

**shipped**

| Set | queries | recall@5 | recall@10 | MRR@10 |
|---|---|---|---|---|
| Cards: catalogue | 23 | 0.80 | 0.88 | 0.82 |
| Cards: in pool | 21 | 0.72 | 0.77 | 0.79 |
| Cards: all | 44 | 0.76 | 0.83 | 0.81 |
| Rules | 45 | 0.93 | 1.00 | 0.76 |

## Side by side (recall@10 / MRR@10)

| Set | vector | keyword | hybrid | shipped |
|---|---|---|---|---|
| Cards: catalogue (23) | 0.83 / 0.58 | 0.29 / 0.21 | 0.77 / 0.57 | 0.88 / 0.82 |
| Cards: in pool (21) | 0.67 / 0.72 | 0.39 / 0.35 | 0.78 / 0.73 | 0.77 / 0.79 |
| Cards: all (44) | 0.75 / 0.65 | 0.33 / 0.28 | 0.78 / 0.65 | 0.83 / 0.81 |
| Rules (45) | 1.00 / 0.76 | 0.43 / 0.24 | 0.73 / 0.46 | 1.00 / 0.76 |
| Cards, kind `name` (2) | 1.00 / 0.62 | 1.00 / 0.75 | 1.00 / 1.00 | 1.00 / 1.00 |
| Cards, kind `need` (21) | 0.67 / 0.72 | 0.39 / 0.35 | 0.78 / 0.73 | 0.77 / 0.79 |
| Cards, kind `paraphrase` (18) | 0.78 / 0.54 | 0.11 / 0.13 | 0.71 / 0.48 | 0.85 / 0.80 |
| Cards, kind `term` (3) | 1.00 / 0.83 | 0.87 / 0.29 | 1.00 / 0.83 | 1.00 / 0.83 |
| Rules, kind `commander` (14) | 1.00 / 0.67 | 0.43 / 0.23 | 0.57 / 0.41 | 1.00 / 0.67 |
| Rules, kind `general` (15) | 1.00 / 0.72 | 0.27 / 0.13 | 0.77 / 0.38 | 1.00 / 0.72 |
| Rules, kind `keyword` (16) | 1.00 / 0.88 | 0.59 / 0.36 | 0.84 / 0.56 | 1.00 / 0.88 |

## Latency

Median per search, including embedding the query with the local model.

| | vector | keyword | hybrid | shipped |
|---|---|---|---|---|
| Cards (ms) | 99 | 6 | 98 | 1248 |
| Rules (ms) | 35 | 7 | 35 | 27 |

## Embedding text variants

Rules: the rule's own text (as embedded), against the same text with its section and its parent's first sentence (ADR 0010's first design).

| Set | queries | recall@5 | recall@10 | MRR@10 |
|---|---|---|---|---|
| Rules, text alone: vector | 45 | 0.93 | 1.00 | 0.76 |
| Rules, with context: vector | 45 | 0.96 | 1.00 | 0.81 |
| Rules, text alone: hybrid | 45 | 0.59 | 0.73 | 0.46 |
| Rules, with context: hybrid | 45 | 0.54 | 0.69 | 0.43 |

## Per query (recall@10)

Misses are the relevant items the shipped configuration didn't return in its top 10.

| Query | vector | keyword | hybrid | shipped | missed by shipped |
|---|---|---|---|---|---|
| C01 an artifact that costs one and taps for two colorless mana | 1.00 | 0.00 | 1.00 | 1.00 |  |
| C02 one-mana white instant that exiles a creature, and its controller gains life equal to its power | 0.50 | 0.50 | 0.50 | 0.50 | Emeritus of Truce // Swords to Plowshares |
| C03 exile a creature, and its controller gets to search for a basic land | 1.00 | 0.00 | 1.00 | 1.00 |  |
| C04 green sorcery that finds two basic lands, one onto the battlefield tapped and one into your hand | 0.25 | 0.00 | 0.25 | 0.50 | Kodama's Reach; Peregrination |
| C05 four-mana sorcery that destroys all creatures, and they can't be regenerated | 1.00 | 0.00 | 1.00 | 1.00 |  |
| C06 counter target spell for two blue mana | 1.00 | 0.00 | 0.00 | 1.00 |  |
| C07 black sorcery that searches your library for any card and puts it into your hand | 0.50 | 0.00 | 0.00 | 1.00 |  |
| C08 equipment that draws two cards when the equipped creature dies | 1.00 | 0.00 | 1.00 | 1.00 |  |
| C09 a land that taps for any color in your commander's color identity | 0.75 | 0.25 | 1.00 | 0.75 | Opal Palace |
| C10 an enchantment that doubles the tokens you create | 1.00 | 0.25 | 1.00 | 1.00 |  |
| C11 sacrifice a creature to add colorless mana | 0.00 | 0.00 | 0.00 | 0.50 | Transmogrant Altar |
| C12 every player sacrifices their creatures, then the creatures in graveyards come back | 0.00 | 0.00 | 0.00 | 0.00 | Living Death; Living End |
| C13 equipment that gives a creature haste and stops it being targeted | 1.00 | 0.00 | 1.00 | 1.00 |  |
| C14 whenever an opponent casts a spell, you draw a card unless they pay one | 1.00 | 0.00 | 1.00 | 1.00 |  |
| C15 whenever an opponent draws a card, you get a Treasure unless they pay two | 1.00 | 0.00 | 1.00 | 1.00 |  |
| C16 bounce a nonland permanent you don't control, or overload it to bounce all of them | 1.00 | 0.00 | 1.00 | 1.00 |  |
| C17 a land that gives you no maximum hand size | 1.00 | 0.00 | 1.00 | 1.00 |  |
| C18 sacrifice a land to put your commander into your hand from the command zone | 1.00 | 1.00 | 1.00 | 1.00 |  |
| C19 ripple | 1.00 | 1.00 | 1.00 | 1.00 |  |
| C20 epic | 1.00 | 0.60 | 1.00 | 1.00 |  |
| C21 gravestorm | 1.00 | 1.00 | 1.00 | 1.00 |  |
| C22 Atraxa | 1.00 | 1.00 | 1.00 | 1.00 |  |
| C23 kodamas reach | 1.00 | 1.00 | 1.00 | 1.00 |  |
| P01 counterspells | 0.80 | 0.00 | 1.00 | 1.00 |  |
| P02 ways to destroy an artifact or enchantment | 0.25 | 0.00 | 0.25 | 0.25 | Deem Inferior; Loran of the Third Path; Woodfall Primus |
| P03 cheap ramp | 0.33 | 0.00 | 0.33 | 0.33 | Bucknard's Everfull Purse; Darksteel Ingot; Luck Bobblehead; Sanctum Weaver |
| P04 repeatable card draw | 0.33 | 0.17 | 0.33 | 0.67 | Case of the Locked Hothouse; Ginger, Queen of Sweets |
| P05 board wipes that kill every creature | 0.57 | 0.00 | 0.71 | 0.71 | Phyrexian Scriptures; Whipflare |
| P06 return a creature card from my graveyard to the battlefield | 0.75 | 0.25 | 0.75 | 0.50 | Anti-Venom, Horrifying Healer; Obsessive Stitcher |
| P07 get cards back from my graveyard to my hand | 0.33 | 0.22 | 0.33 | 0.44 | Evolution Charm; Evolution Witness; Kami of the Palace Fields; Maestros Confluence; Palace Siege |
| P08 make Treasure tokens | 1.00 | 0.33 | 1.00 | 1.00 |  |
| P09 give my creature hexproof, shroud or ward | 1.00 | 0.25 | 1.00 | 1.00 |  |
| P10 cheap burn to kill a creature | 0.75 | 0.00 | 1.00 | 1.00 |  |
| P11 steal an opponent's creature | 0.00 | 0.00 | 0.00 | 0.00 | Illicit Auction; Might Makes Right; Swooping Pteranodon |
| P12 tutor: search my library for a creature card | 1.00 | 1.00 | 1.00 | 1.00 |  |
| P13 lands that tap for two colors | 1.00 | 0.75 | 1.00 | 0.75 | Shivan Oasis |
| P14 mana rocks that tap for any color | 1.00 | 1.00 | 1.00 | 1.00 |  |
| P15 equipment that gives haste | 1.00 | 1.00 | 1.00 | 1.00 |  |
| P16 stop opposing creatures from blocking | 0.83 | 0.33 | 1.00 | 0.83 | Forgehammer Centurion |
| P17 mill an opponent | 0.75 | 0.50 | 1.00 | 1.00 |  |
| P18 tap down opposing creatures | 0.33 | 0.33 | 0.67 | 0.67 | The Wondrous Wasp |
| P19 flicker a creature: exile it and return it to the battlefield | 0.50 | 0.00 | 1.00 | 1.00 |  |
| P20 take an extra turn | 0.50 | 1.00 | 1.00 | 1.00 |  |
| P21 copy my instant and sorcery spells | 1.00 | 1.00 | 1.00 | 1.00 |  |
| R01 How many cards must a Commander deck have? | 1.00 | 1.00 | 1.00 | 1.00 |  |
| R02 Can I run more than one copy of a card in Commander? | 1.00 | 0.00 | 0.00 | 1.00 |  |
| R03 Which cards can go in my deck, given my commander's colors? | 1.00 | 1.00 | 1.00 | 1.00 |  |
| R04 Does reminder text count toward a card's color identity? | 1.00 | 0.00 | 0.00 | 1.00 |  |
| R05 Does the back face of a double-faced card count for color identity? | 1.00 | 0.00 | 1.00 | 1.00 |  |
| R06 What life total do players start with in Commander? | 1.00 | 0.00 | 0.00 | 1.00 |  |
| R07 How much more does my commander cost each time I recast it from the command zone? | 1.00 | 1.00 | 1.00 | 1.00 |  |
| R08 What happens when my commander would die or be exiled? | 1.00 | 0.00 | 1.00 | 1.00 |  |
| R09 Can my commander go to the command zone instead of my hand or library? | 1.00 | 1.00 | 1.00 | 1.00 |  |
| R10 How much commander damage makes a player lose? | 1.00 | 0.00 | 0.00 | 1.00 |  |
| R11 Can a Vehicle be my commander? | 1.00 | 1.00 | 1.00 | 1.00 |  |
| R12 A planeswalker says it can be your commander. Is that allowed? | 1.00 | 0.00 | 0.00 | 1.00 |  |
| R13 Where does my commander start the game? | 1.00 | 0.00 | 0.00 | 1.00 |  |
| R14 Can I include a land with a basic land type that makes a color outside my commander's identity? | 1.00 | 1.00 | 1.00 | 1.00 |  |
| R15 How do partner commanders work? | 1.00 | 1.00 | 1.00 | 1.00 |  |
| R16 Is the first mulligan free in a multiplayer game? | 1.00 | 1.00 | 1.00 | 1.00 |  |
| R17 What does deathtouch do? | 1.00 | 1.00 | 1.00 | 1.00 |  |
| R18 How does a creature with trample assign its combat damage? | 1.00 | 1.00 | 1.00 | 1.00 |  |
| R19 How does lifelink work? | 1.00 | 0.00 | 1.00 | 1.00 |  |
| R20 What does hexproof mean? | 1.00 | 1.00 | 1.00 | 1.00 |  |
| R21 Can an indestructible creature be destroyed by damage? | 1.00 | 0.00 | 0.00 | 1.00 |  |
| R22 When can I cast a spell with flash? | 1.00 | 0.00 | 0.00 | 1.00 |  |
| R23 What does ward do when my creature is targeted? | 1.00 | 0.00 | 1.00 | 1.00 |  |
| R24 How does cascade work? | 1.00 | 1.00 | 1.00 | 1.00 |  |
| R25 How does storm copy a spell? | 1.00 | 0.00 | 1.00 | 1.00 |  |
| R26 How does flashback let me cast a card from my graveyard? | 1.00 | 1.00 | 1.00 | 1.00 |  |
| R27 What does it mean to mill cards? | 1.00 | 0.00 | 1.00 | 1.00 |  |
| R28 What happens to a permanent when it phases out? | 1.00 | 1.00 | 1.00 | 1.00 |  |
| R29 How does ninjutsu work? | 1.00 | 1.00 | 1.00 | 1.00 |  |
| R30 What does proliferate do? | 1.00 | 1.00 | 1.00 | 1.00 |  |
| R31 How does double strike deal damage in combat? | 1.00 | 0.50 | 0.50 | 1.00 |  |
| R32 What happens if I control two legendary permanents with the same name? | 1.00 | 0.00 | 1.00 | 1.00 |  |
| R33 What happens to a creature with 0 toughness? | 1.00 | 0.00 | 1.00 | 1.00 |  |
| R34 What happens if I have to draw from an empty library? | 1.00 | 0.00 | 0.00 | 1.00 |  |
| R35 When does a player lose the game for having no life? | 1.00 | 0.00 | 0.00 | 1.00 |  |
| R36 Why can't my creature use a tap ability the turn it enters? | 1.00 | 0.00 | 1.00 | 1.00 |  |
| R37 What happens to a token that leaves the battlefield? | 1.00 | 0.00 | 0.50 | 1.00 |  |
| R38 What happens to a spell that gets countered? | 1.00 | 0.00 | 0.00 | 1.00 |  |
| R39 What is the mana value of a card with X in its cost? | 1.00 | 0.00 | 1.00 | 1.00 |  |
| R40 How many lands can I play each turn? | 1.00 | 0.00 | 1.00 | 1.00 |  |
| R41 What is the maximum hand size, and when do I discard down to it? | 1.00 | 1.00 | 1.00 | 1.00 |  |
| R42 Which spell on the stack resolves first? | 1.00 | 0.00 | 1.00 | 1.00 |  |
| R43 How do extra turns work? | 1.00 | 1.00 | 1.00 | 1.00 |  |
| R44 What is a Treasure token? | 1.00 | 1.00 | 1.00 | 1.00 |  |
| R45 What does it mean to sacrifice a permanent? | 1.00 | 0.00 | 1.00 | 1.00 |  |

## Caveats

- **Small set.** 44 card queries and 45 rules questions: one query moves a set's recall by about 2 points, and a kind with 2 queries by 50. Differences of a few points are noise.
- **Labels.** Drafted by Claude from checkable sources and reviewed by the owner, who made no corrections. Catalogue queries list known targets, not every relevant card in 32,000, so their recall can understate a search that finds other good answers. Pool queries were judged against all 300 pool cards.
- **Overlap with model selection.** Several catalogue queries describe cards the embedding model was chosen on (ADR 0009: Sol Ring, Command Tower, Rhystic Study, Smothering Tithe, Cultivate, Swords to Plowshares, Lightning Greaves, Demonic Tutor, Wrath of God, Counterspell), in different words. The model choice may flatter vector search on those.
- **Variant search path.** Variants are embedded into a temporary table and searched exactly; the shipped configuration goes through the app's real search path. If anything, that favours the variants.
- **Selection on the test set.** The defaults below were chosen on this same set, so the shipped configuration's numbers are somewhat optimistic. There's no held-out set yet; any further tuning (fusion weights, keyword query form) needs one first.

## Findings

_Written by hand. The **dev** set, with the configuration #76 ships. Retrieval was tuned on this set, so these numbers are optimistic. The honest measure is the held-out set (`2026-10-04-retrieval-test.md`)._

| Dev | Start of the improvement work (#70) | Final (`shipped`) |
|---|---|---|
| Cards, all (44): recall@10 / MRR@10 | 0.58 / 0.45 | **0.83 / 0.81** |
| Rules (45): recall@10 / MRR@10 | 0.97 / 0.70 | **1.00 / 0.76** |
| Median card search | 91 ms | 1,248 ms |

- **Every adopted lever was chosen here, by the adoption rule** (`evals/reports/retrieval-experiments/`):
  - the 8b embedder
  - the half-weight keyword arm
  - summary vectors for pool searches
  - `qwen3:8b` reranking
- **On the held-out set, the same configuration gains less:** cards recall@10 rose 9 points there against 25 here, and MRR rose 25 against 36.
- **The held-out run also shows a regression** on single-word mechanic searches that the three dev term queries didn't.
