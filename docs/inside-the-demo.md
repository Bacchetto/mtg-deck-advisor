# Inside the demo: what the agent did, turn by turn

The demo says "done after 4 turns, $0.0000". This page shows what those turns were, from the
recordings the demo replays ([`recordings/demo/`](../recordings/demo/)). The costs are what each
call cost when it was recorded live on Claude Sonnet 5.5, priced by the project's own pricing
table ([`llm/pricing.py`](../src/mtg_deck_advisor/llm/pricing.py)). The demo shows `$0.0000`
because a replay calls no model and bills nothing.

## What a turn is

A turn is **one call to the model** ([`agent/loop.py`](../src/mtg_deck_advisor/agent/loop.py)):
1. The loop sends Claude the whole conversation so far: the task, its earlier replies, and every
   tool result.
2. Claude replies, either asking for tools or giving its answer.
3. If it asked for tools, code runs every one of them and sends all the results back together.
   That starts the next turn.
4. A reply with no tool calls is the answer, and the run ends.

A turn isn't a tool call: one turn can ask for several tools at once. The 30-turn limit counts
model calls.

## Drafting the deck: 4 turns, $0.103

You pressed Enter, so the agent chose what to build.

**The agent starts with a list of every possible commander.** Before the first turn, code finds
each card in your pool that can legally lead a Commander deck, and lists them in the agent's
first message by name and colors: 201 of them in the demo
([`_draft_prompt`](../src/mtg_deck_advisor/agent/flows.py)):

```
Draft a Commander deck from my card pool.
Choose the commander my pool supports best.

My pool has 2057 cards (1396 different). These can be the commander:
<untrusted>
Aang, Swift Savior // Aang and La, Ocean's Fury (WU)
Aang, the Last Airbender (W)
Adeline, Resplendent Cathar (W)
...
</untrusted>
```

So the agent picks its commander from that list, by name, before reading any card. Its first turn
then reads that card in full with `get_card`, since the list gives only names and colors. In the
same turn it searches for what a deck around the card needs. Its reasons for the choice were in
its thinking, which the API returns encrypted, so the recording doesn't show them. The final
answer explains the choice instead.

| Turn | What Claude asked for | What came back | Sent to Claude | Cost |
|---|---|---|---|---|
| 1 | `get_card` Sheoldred, Whispering One, and 4 pool searches: reanimation, ramp, removal and card draw | Sheoldred's card text, and 25, 20, 25 and 20 matching cards from your pool | 5,900 tokens | $0.007 |
| 2 | 3 more searches: creatures with strong enter or death effects, mana rocks, and self-mill and tutors | 40, 20 and 20 more cards | 14,900 tokens | $0.028 |
| 3 | `propose_deck`: Sheoldred and 99 cards (66 cards and 33 Swamps), with its reasons | "Proposal 1 is legal and is now waiting for the user's approval." | 23,400 tokens | $0.050 |
| 4 | Nothing: the written answer you see | | 26,100 tokens | $0.018 |

- **It picked Sheoldred in the first turn.** It looked the card up and searched for what a
  reanimator deck needs at the same time.
- **Its first proposal passed.** Code checked all 100 cards against the Commander rules and your
  collection. A proposal that fails comes back with every problem listed, and fixing it costs
  another turn.
- **Turn 3 cost the most,** because writing out a 100-card decklist is a lot of output.

## Changing the deck: 4 turns, $0.053

You typed "Add more card draw and cut the weakest creatures."

| Turn | What Claude asked for | What came back | Sent to Claude | Cost |
|---|---|---|---|---|
| 1 | `analyze_deck` and `find_by_role` (card draw) | The saved deck's role counts, curve and lands, and the 36 card-draw cards in your pool that fit the deck's colors | 4,800 tokens | $0.013 |
| 2 | `propose_changes`: cut 5 creatures, add 5 card-draw cards, with the goals it set | "Proposal 1 is legal and is now waiting for the user's approval." | 10,500 tokens | $0.023 |
| 3 | `analyze_deck` again, to check its proposal against its goals | Before and after: card draw from 9 to 14, average mana value from 3.46 to 3.32, lands still 36 | 11,300 tokens | $0.005 |
| 4 | Nothing: the written answer | | 13,200 tokens | $0.012 |

- **It checked its own work before answering,** in turn 3. The goals it set (more card draw, a
  creature cut, the same lands, a lower average cost) are ones code can check.
- **It didn't search with free text.** `find_by_role` lists the pool's cards with a given role,
  and it's offered for changes only.

## The rules question: 2 turns, $0.003

"How much extra does it cost to cast my commander for the third time?"

| Turn | What Claude asked for | What came back | Cost |
|---|---|---|---|
| 1 | `search_rules`: "commander tax additional cost for each time cast from command zone" | 5 rules, the best match first: 903.8 | $0.001 |
| 2 | Nothing: the answer, citing 903.8 | | $0.002 |

The answer is shown only because rule 903.8 was retrieved in this run. An answer citing a rule it
didn't retrieve, or none at all, comes back as "not found".

## What the numbers show

- **The conversation grows every turn.** Each call re-sends everything so far, so the draft's
  last turn sent 26,100 tokens to write a short answer. This is why a run that takes more turns
  costs more than you'd expect: Haiku took 17 turns a draft against Sonnet's 5 and cost more per
  deck, though its tokens cost half as much ([write-up](findings/haiku-costs-more.md)).
- **Caching makes the growth affordable.** Most of what's re-sent was sent before, so it's read
  from the prompt cache at a tenth of the input price. In the draft's last turn, 23,300 of the
  26,100 tokens were cache reads.
- **Card and rule text is marked as data.** Every tool result that carries text from cards or
  rules wraps it in `<untrusted>` delimiters, so the model is told not to follow instructions in
  it.

## Look for yourself

Each file in [`recordings/demo/`](../recordings/demo/) is one model call: the request (the
system prompt, the conversation so far and the tools offered) and Claude's response (its text,
tool calls and token counts). The file name is a hash of the request, which is how a replay
finds the response to give back.
