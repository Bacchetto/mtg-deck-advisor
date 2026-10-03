# Retrieval eval, 2026-10-02

Eval set: `evals/datasets/retrieval_cards.csv` (44 card queries) and `evals/datasets/retrieval_rules.csv` (45 rules questions), owner-reviewed, written before the search code. Embedding model: `qwen3-embedding:0.6b`. Script: `scripts/evaluate_retrieval.py`. Each search returns its top 10; MRR counts 0 when nothing relevant is in them.

## Results by mode

**vector**

| Set | queries | recall@5 | recall@10 | MRR@10 |
|---|---|---|---|---|
| Cards: catalogue | 23 | 0.52 | 0.56 | 0.51 |
| Cards: in pool | 21 | 0.36 | 0.49 | 0.48 |
| Cards: all | 44 | 0.44 | 0.53 | 0.49 |
| Rules | 45 | 0.90 | 0.97 | 0.71 |

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
| Cards: catalogue | 23 | 0.50 | 0.59 | 0.42 |
| Cards: in pool | 21 | 0.39 | 0.57 | 0.48 |
| Cards: all | 44 | 0.45 | 0.58 | 0.45 |
| Rules | 45 | 0.53 | 0.74 | 0.46 |

## Side by side (recall@10 / MRR@10)

| Set | vector | keyword | hybrid |
|---|---|---|---|
| Cards: catalogue (23) | 0.56 / 0.51 | 0.29 / 0.21 | 0.59 / 0.42 |
| Cards: in pool (21) | 0.49 / 0.48 | 0.39 / 0.35 | 0.57 / 0.48 |
| Cards: all (44) | 0.53 / 0.49 | 0.33 / 0.28 | 0.58 / 0.45 |
| Rules (45) | 0.97 / 0.71 | 0.43 / 0.24 | 0.74 / 0.46 |
| Cards, kind `name` (2) | 1.00 / 0.75 | 1.00 / 0.75 | 1.00 / 1.00 |
| Cards, kind `need` (21) | 0.49 / 0.48 | 0.39 / 0.35 | 0.57 / 0.48 |
| Cards, kind `paraphrase` (18) | 0.46 / 0.42 | 0.11 / 0.13 | 0.47 / 0.32 |
| Cards, kind `term` (3) | 0.87 / 0.83 | 0.87 / 0.29 | 1.00 / 0.61 |
| Rules, kind `commander` (14) | 0.93 / 0.63 | 0.43 / 0.23 | 0.64 / 0.39 |
| Rules, kind `general` (15) | 1.00 / 0.81 | 0.27 / 0.13 | 0.70 / 0.40 |
| Rules, kind `keyword` (16) | 0.97 / 0.70 | 0.59 / 0.36 | 0.88 / 0.57 |

## Latency

Median per search, including embedding the query with the local model.

| | vector | keyword | hybrid |
|---|---|---|---|
| Cards (ms) | 86 | 5 | 87 |
| Rules (ms) | 15 | 6 | 22 |

## Embedding text variants

Rules: the rule's own text (as embedded), against the same text with its section and its parent's first sentence (ADR 0010's first design).

| Set | queries | recall@5 | recall@10 | MRR@10 |
|---|---|---|---|---|
| Rules, text alone: vector | 45 | 0.90 | 0.97 | 0.71 |
| Rules, with context: vector | 45 | 0.81 | 0.91 | 0.66 |
| Rules, text alone: hybrid | 45 | 0.53 | 0.74 | 0.46 |
| Rules, with context: hybrid | 45 | 0.49 | 0.68 | 0.42 |

Cards: ADR 0009's text (name, type, rules text), against the same text plus the mana cost.

| Set | queries | recall@5 | recall@10 | MRR@10 |
|---|---|---|---|---|
| Cards, measured text: vector | 44 | 0.44 | 0.53 | 0.49 |
| Cards, with cost: vector | 44 | 0.30 | 0.42 | 0.37 |
| Cards, measured text: hybrid | 44 | 0.45 | 0.58 | 0.45 |
| Cards, with cost: hybrid | 44 | 0.37 | 0.51 | 0.39 |

## Per query (recall@10)

Misses are the relevant items hybrid search didn't return in its top 10.

| Query | vector | keyword | hybrid | missed by hybrid |
|---|---|---|---|---|
| C01 an artifact that costs one and taps for two colorless mana | 0.00 | 0.00 | 0.00 | Sol Ring |
| C02 one-mana white instant that exiles a creature, and its controller gains life equal to its power | 0.50 | 0.50 | 0.50 | Emeritus of Truce // Swords to Plowshares |
| C03 exile a creature, and its controller gets to search for a basic land | 1.00 | 0.00 | 1.00 |  |
| C04 green sorcery that finds two basic lands, one onto the battlefield tapped and one into your hand | 0.25 | 0.00 | 0.25 | Flare of Cultivation; Kodama's Reach; Peregrination |
| C05 four-mana sorcery that destroys all creatures, and they can't be regenerated | 0.00 | 0.00 | 0.50 | Wrath of God |
| C06 counter target spell for two blue mana | 0.00 | 0.00 | 0.00 | Counterspell |
| C07 black sorcery that searches your library for any card and puts it into your hand | 0.00 | 0.00 | 0.00 | Demonic Tutor; Diabolic Tutor |
| C08 equipment that draws two cards when the equipped creature dies | 0.00 | 0.00 | 1.00 |  |
| C09 a land that taps for any color in your commander's color identity | 0.50 | 0.25 | 0.75 | Hidden Hideout |
| C10 an enchantment that doubles the tokens you create | 1.00 | 0.25 | 0.50 | Anointed Procession; Parallel Lives |
| C11 sacrifice a creature to add colorless mana | 0.00 | 0.00 | 0.00 | Ashnod's Altar; Transmogrant Altar |
| C12 every player sacrifices their creatures, then the creatures in graveyards come back | 0.00 | 0.00 | 0.00 | Living Death; Living End |
| C13 equipment that gives a creature haste and stops it being targeted | 0.00 | 0.00 | 0.00 | Lightning Greaves; Swiftfoot Boots |
| C14 whenever an opponent casts a spell, you draw a card unless they pay one | 1.00 | 0.00 | 1.00 |  |
| C15 whenever an opponent draws a card, you get a Treasure unless they pay two | 1.00 | 0.00 | 0.00 | Smothering Tithe |
| C16 bounce a nonland permanent you don't control, or overload it to bounce all of them | 1.00 | 0.00 | 1.00 |  |
| C17 a land that gives you no maximum hand size | 1.00 | 0.00 | 1.00 |  |
| C18 sacrifice a land to put your commander into your hand from the command zone | 1.00 | 1.00 | 1.00 |  |
| C19 ripple | 1.00 | 1.00 | 1.00 |  |
| C20 epic | 0.60 | 0.60 | 1.00 |  |
| C21 gravestorm | 1.00 | 1.00 | 1.00 |  |
| C22 Atraxa | 1.00 | 1.00 | 1.00 |  |
| C23 kodamas reach | 1.00 | 1.00 | 1.00 |  |
| P01 counterspells | 0.80 | 0.00 | 0.80 | Confirm Suspicions |
| P02 ways to destroy an artifact or enchantment | 0.25 | 0.00 | 0.25 | Deem Inferior; Loran of the Third Path; Woodfall Primus |
| P03 cheap ramp | 0.00 | 0.00 | 0.00 | Bucknard's Everfull Purse; Commander's Sphere; Darksteel Ingot; Luck Bobblehead; Return from the Wilds; Sanctum Weaver |
| P04 repeatable card draw | 0.33 | 0.17 | 0.33 | Case of the Locked Hothouse; Ginger, Queen of Sweets; Level Up; Thickest in the Thicket |
| P05 board wipes that kill every creature | 0.14 | 0.00 | 0.14 | Decree of Annihilation; Destructive Force; Do or Die; Game Over; Phyrexian Scriptures; Whipflare |
| P06 return a creature card from my graveyard to the battlefield | 0.25 | 0.25 | 0.25 | Anti-Venom, Horrifying Healer; Obsessive Stitcher; Reunion of the House |
| P07 get cards back from my graveyard to my hand | 0.11 | 0.22 | 0.22 | Dark Intimations; Dawn-Blessed Pennant; Evolution Witness; Invasion of Shandalar // Leyline Surge; Kami of the Palace Fields; Maestros Confluence; Palace Siege |
| P08 make Treasure tokens | 1.00 | 0.33 | 1.00 |  |
| P09 give my creature hexproof, shroud or ward | 0.50 | 0.25 | 0.50 | Crystal Carapace; Magic Damper |
| P10 cheap burn to kill a creature | 0.50 | 0.00 | 0.25 | Jaya's Greeting; Thundering Rebuke; Wild Slash |
| P11 steal an opponent's creature | 0.00 | 0.00 | 0.00 | Illicit Auction; Might Makes Right; Swooping Pteranodon |
| P12 tutor: search my library for a creature card | 1.00 | 1.00 | 1.00 |  |
| P13 lands that tap for two colors | 1.00 | 0.75 | 1.00 |  |
| P14 mana rocks that tap for any color | 1.00 | 1.00 | 1.00 |  |
| P15 equipment that gives haste | 0.50 | 1.00 | 1.00 |  |
| P16 stop opposing creatures from blocking | 0.33 | 0.33 | 0.17 | Abandon the Post; Bellowing Bruiser // Beat a Path; Forgehammer Centurion; Nacatl Hunt-Pride; Unearthly Blizzard |
| P17 mill an opponent | 0.25 | 0.50 | 0.50 | Tenured Oilcaster; Vexing Arcanix |
| P18 tap down opposing creatures | 0.33 | 0.33 | 1.00 |  |
| P19 flicker a creature: exile it and return it to the battlefield | 1.00 | 0.00 | 1.00 |  |
| P20 take an extra turn | 0.50 | 1.00 | 0.50 | Teferi, Master of Time |
| P21 copy my instant and sorcery spells | 0.50 | 1.00 | 1.00 |  |
| R01 How many cards must a Commander deck have? | 1.00 | 1.00 | 1.00 |  |
| R02 Can I run more than one copy of a card in Commander? | 0.00 | 0.00 | 0.00 | 903.5b |
| R03 Which cards can go in my deck, given my commander's colors? | 1.00 | 1.00 | 1.00 |  |
| R04 Does reminder text count toward a card's color identity? | 1.00 | 0.00 | 0.00 | 903.4c |
| R05 Does the back face of a double-faced card count for color identity? | 1.00 | 0.00 | 1.00 |  |
| R06 What life total do players start with in Commander? | 1.00 | 0.00 | 0.00 | 903.7 |
| R07 How much more does my commander cost each time I recast it from the command zone? | 1.00 | 1.00 | 1.00 |  |
| R08 What happens when my commander would die or be exiled? | 1.00 | 0.00 | 1.00 |  |
| R09 Can my commander go to the command zone instead of my hand or library? | 1.00 | 1.00 | 1.00 |  |
| R10 How much commander damage makes a player lose? | 1.00 | 0.00 | 0.00 | 704.6c; 903.10a |
| R11 Can a Vehicle be my commander? | 1.00 | 1.00 | 1.00 |  |
| R12 A planeswalker says it can be your commander. Is that allowed? | 1.00 | 0.00 | 0.00 | 903.3a |
| R13 Where does my commander start the game? | 1.00 | 0.00 | 1.00 |  |
| R14 Can I include a land with a basic land type that makes a color outside my commander's identity? | 1.00 | 1.00 | 1.00 |  |
| R15 How do partner commanders work? | 1.00 | 1.00 | 1.00 |  |
| R16 Is the first mulligan free in a multiplayer game? | 1.00 | 1.00 | 1.00 |  |
| R17 What does deathtouch do? | 1.00 | 1.00 | 1.00 |  |
| R18 How does a creature with trample assign its combat damage? | 1.00 | 1.00 | 1.00 |  |
| R19 How does lifelink work? | 1.00 | 0.00 | 1.00 |  |
| R20 What does hexproof mean? | 1.00 | 1.00 | 1.00 |  |
| R21 Can an indestructible creature be destroyed by damage? | 1.00 | 0.00 | 1.00 |  |
| R22 When can I cast a spell with flash? | 1.00 | 0.00 | 0.00 | 702.8a |
| R23 What does ward do when my creature is targeted? | 1.00 | 0.00 | 0.00 | 702.21a |
| R24 How does cascade work? | 1.00 | 1.00 | 1.00 |  |
| R25 How does storm copy a spell? | 1.00 | 0.00 | 1.00 |  |
| R26 How does flashback let me cast a card from my graveyard? | 1.00 | 1.00 | 1.00 |  |
| R27 What does it mean to mill cards? | 1.00 | 0.00 | 1.00 |  |
| R28 What happens to a permanent when it phases out? | 0.50 | 1.00 | 1.00 |  |
| R29 How does ninjutsu work? | 1.00 | 1.00 | 1.00 |  |
| R30 What does proliferate do? | 1.00 | 1.00 | 1.00 |  |
| R31 How does double strike deal damage in combat? | 1.00 | 0.50 | 1.00 |  |
| R32 What happens if I control two legendary permanents with the same name? | 1.00 | 0.00 | 1.00 |  |
| R33 What happens to a creature with 0 toughness? | 1.00 | 0.00 | 0.00 | 704.5f |
| R34 What happens if I have to draw from an empty library? | 1.00 | 0.00 | 0.00 | 704.5b |
| R35 When does a player lose the game for having no life? | 1.00 | 0.00 | 0.00 | 704.5a |
| R36 Why can't my creature use a tap ability the turn it enters? | 1.00 | 0.00 | 0.00 | 302.6 |
| R37 What happens to a token that leaves the battlefield? | 1.00 | 0.00 | 0.50 | 704.5d |
| R38 What happens to a spell that gets countered? | 1.00 | 0.00 | 1.00 |  |
| R39 What is the mana value of a card with X in its cost? | 1.00 | 0.00 | 1.00 |  |
| R40 How many lands can I play each turn? | 1.00 | 0.00 | 1.00 |  |
| R41 What is the maximum hand size, and when do I discard down to it? | 1.00 | 1.00 | 1.00 |  |
| R42 Which spell on the stack resolves first? | 1.00 | 0.00 | 1.00 |  |
| R43 How do extra turns work? | 1.00 | 1.00 | 1.00 |  |
| R44 What is a Treasure token? | 1.00 | 1.00 | 1.00 |  |
| R45 What does it mean to sacrifice a permanent? | 1.00 | 0.00 | 1.00 |  |

## Caveats

- **Small set.** 44 card queries and 45 rules questions: one query moves a set's recall by about 2 points, and a kind with 2 queries by 50. Differences of a few points are noise.
- **Labels.** Drafted by Claude from checkable sources and reviewed by the owner, who made no corrections. Catalogue queries list known targets, not every relevant card in 32,000, so their recall can understate a search that finds other good answers. Pool queries were judged against all 300 pool cards.
- **Overlap with model selection.** Several catalogue queries describe cards the embedding model was chosen on (ADR 0009: Sol Ring, Command Tower, Rhystic Study, Smothering Tithe, Cultivate, Swords to Plowshares, Lightning Greaves, Demonic Tutor, Wrath of God, Counterspell), in different words. The model choice may flatter vector search on those.

- **Variant search path.** Variants are embedded into a temporary table and searched exactly; the shipped configuration goes through the app's real search path. If anything, that favours the variants.
- **Selection on the test set.** The defaults below were chosen on this same set, so the shipped configuration's numbers are somewhat optimistic. There's no held-out set yet; any further tuning (fusion weights, keyword query form) needs one first.

## Findings

_Written by hand after reading the numbers above. These decided the shipped defaults._

1. **Rules: vector search is strong, and ships as the default.** Recall@10 is 0.97 and MRR 0.71: the answering rule is in the top 10 for 44 of 45 questions. The one complete miss is R02 ("more than one copy of a card"), whose rule (903.5b) says "a different English name" and never "copy".
2. **Rules: hybrid search is a clear loss** (recall@10 0.74 against 0.97). Rules questions are sentences of common words ("player", "creature", "game", "turn"), and keyword search, matching any of them, ranks rules that are long and wordy rather than relevant. It drags right answers out of the fused top 10 (R04, R06, R10, R21–R23, R33–R36, R38).
3. **Rules: the section and parent context hurt** (recall@10 0.91 against 0.97 for the rule's text alone, in vector mode; 0.68 against 0.74 in hybrid). A plausible reason: every rule in a section shares the same heading and its subrules share the parent's sentence, which pulls their vectors together and blurs what distinguishes them. ADR 0010 is updated: one chunk per rule stays, the context goes.
4. **Cards: hybrid ships as the default, on a small margin.** Recall@10 is 0.58 against 0.53 for vector (about two queries' worth), but MRR is lower (0.45 against 0.49). Hybrid rescues exact names and terms (C08 Skullclamp, C20 epic, C05 Wrath of God, P15, P18, P21), and loses some paraphrases that vector search had right (C10 token doubling, C15 Smothering Tithe, P10 cheap burn). The agent reads all 10 results, so recall@10 matters more than rank, and exact names and mechanics are what an agent will often search for. It's a judgment call the numbers don't settle.
5. **Cards: the mana cost in the embedded text is a loss** (recall@10 0.42 against 0.53), confirming the decision made on one spot check in #66.
6. **Cards: paraphrases and deck-building needs are the weak spot.** Paraphrase recall@10 is 0.47, with six catalogue queries at 0 in every mode (C01 Sol Ring, C06 Counterspell, C07 Demonic Tutor, C11–C13). "Need" queries over the pool reach 0.57, with P03 "cheap ramp" and P11 "steal a creature" at 0. Two levers for later, both measurable on this set: reranking the top candidates (RAG-6, Milestone 9), and for needs, the role tags from Milestone 3 (`ramp`, `removal`...), which answer "cheap ramp" exactly when combined with a mana value filter. The agent in Milestone 5 can use both search and role filters.
7. **Keyword search alone is poor** (recall@10 0.33 for cards, 0.43 for rules) except on exact terms and names, where it matches vector search. Its value is as an arm of hybrid search for cards.
8. **Latency is interactive:** a median of about 86 ms per card search and 15 ms per rules search, nearly all of it embedding the query.
