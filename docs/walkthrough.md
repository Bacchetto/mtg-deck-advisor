# Walkthrough script and shot list

A script for the 3-5 minute recorded walkthrough (DEM-4). It runs about **4 minutes 45 seconds**
at an easy speaking pace (150 words a minute, with time to type and scroll), in eight scenes:
1. The problem
2. Starting the demo
3. Drafting a deck
4. Changing the deck
5. A rules question
6. How it decides
7. How it's measured
8. Trade-offs, and close

Everything on screen is the free demo from the [README](../README.md#try-the-demo), replayed from
a recording, plus pages from this repository. Nothing needs an API key, and every run of it looks
the same, so a scene can be recorded again without the output changing.

## Before recording

- **Start the demo first,** off camera, in a fresh clone, so your own `.env` isn't overwritten.
  The image build takes a few minutes, and nobody needs to watch it:
  ```bash
  git clone https://github.com/Bacchetto/mtg-deck-advisor.git mtg-demo
  cd mtg-demo
  cp .env.demo .env
  docker compose up -d --build --wait
  ```
  Stop your development stack first if it's running (`docker compose down`, which keeps its
  data): the demo uses ports 5432 and 8000.
- **Make the terminal readable:** a font of at least 18 pt, about 100 columns wide, a dark theme,
  and a cleared screen with a short prompt.
- **Open these browser tabs, in order:**
  1. The repository's README, at the top.
  2. The README's diagram ("How it works").
  3. The README's "Results" section.
  4. [`docs/findings/agent-sdk-comparison.md`](findings/agent-sdk-comparison.md).
  5. The README's "Known limitations".
- **Expect `$0.0000` on screen.** The demo replays recorded responses, so nothing is billed. The
  script says so, so it doesn't look like a bug.
- **Answers to type in the demo,** in order: `1`, Enter, `a`, `c`,
  `Add more card draw and cut the weakest creatures.`, `a`, `e`, Enter, `q`.

## The script

Times are where each scene starts. **Show** is what's on screen; **Say** is the narration.

### 1. The problem (0:00)

**Show:** the README, at the top.

**Say:**
> This is MTG Deck Advisor. It builds a legal Magic: The Gathering Commander deck from the cards
> you own, and answers rules questions with citations. A Commander deck is a hundred cards, one
> of each, in your commander's colors. A model is good at the creative part and bad at the
> bookkeeping, so here the model proposes, and code decides.

### 2. Starting the demo (0:27)

**Show:** the terminal, with the demo already up. Type `docker compose ps` to show the database and
app running and healthy.

**Say:**
> The demo needs only Docker: no API key and no GPU. It replays a recorded session, so every model
> call and search gives exactly what it gave live, and nothing is billed. That's why you'll see
> zero dollars. It's seeded with a real collection of just over two thousand cards.

### 3. Drafting a deck (0:52)

**Show:** type `docker compose exec app mtg-advisor build`.
- At `Which pool?`, type `1`.
- At the "built around" prompt, press Enter.
- While it shows `the agent is working...` and the turn counter, keep talking.
- When the answer appears, scroll slowly through the plan, then point at the line "The deck passed
  the legality check and is waiting for your approval. Nothing is saved until you approve it."
- Scroll past the decklist to the menu, and type `a`. Show `approved and saved as version 1`.

**Say:**
> I'll pick the demo collection, and let the agent choose. It's Claude Sonnet, with tools to
> search my cards and the official rules. In four turns it picked Sheoldred, a black reanimator
> commander, and proposed a hundred cards.
>
> Notice: the deck passed the legality check, and nothing is saved until I approve it. The agent
> can't save anything. Code checked every Commander rule, and that I own every card. A broken
> rule would have gone back to the agent with the whole list of problems. I'll approve it:
> version one.

### 4. Changing the deck (1:48)

**Show:** type `c`, then `Add more card draw and cut the weakest creatures.`
- When the answer appears, point at "Goals I set", then at the before-and-after table.
- Show the `- ` and `+ ` lines of the swap, then type `a` (`saved as version 2`).
- Type `e` and press Enter, to show `exported version 2 to sheoldred-whispering-one.txt`. Type `q`.

**Say:**
> Now a change, in plain words: more card draw, cut the weakest creatures. The agent turns that
> into goals it can check: more card-draw cards, at least one creature cut, the same land count,
> a lower average cost. Then it checks its own proposal against those goals with an analysis
> tool, before it hands it to me. Card draw goes from nine to fourteen. I approve it, that's
> version two, and I export the decklist. Every approval and export is in an audit log.

### 5. A rules question (2:36)

**Show:** type
`docker compose exec app mtg-advisor ask "How much extra does it cost to cast my commander for the third time?"`.
Point at the cited rule, `[903.8]`, under the answer.

**Say:**
> It answers rules questions too. Four extra mana, and it cites rule 903.8. The answer is only
> shown if every rule it cites was actually retrieved in that run. If not, you get "not found", so
> it can't answer from memory or invent a rule number.

### 6. How it decides (3:00)

**Show:** the README's diagram. Point, in order, at the agent loop, the "Propose" tools, the
guardrails box and its two arrows, and "Your decisions".

**Say:**
> Here's the shape of it. The agent runs in a loop with a turn limit, and a cost cap checked
> before each model call, so a run can't overspend. Its tools can read and propose, nothing else.
> Valid proposals come to me; invalid ones go back with their problems. Approving and exporting
> aren't tools at all, so no instruction hidden in card text can change a deck. It also runs as
> an MCP server in Claude Code, where decisions go through the client's own permission prompt.

### 7. How it's measured (3:39)

**Show:** the README's "Results" section. Scroll from the retrieval table to the model
comparison, then to "Prompt injection". Then switch to the Agent SDK comparison's "The numbers"
table.

**Say:**
> Everything is measured. On fifty-five rules questions, Sonnet was ninety-nine percent correct.
> One surprise: the cheaper model, Haiku, cost forty-six percent more per deck, because it kept
> proposing illegal decks, and each fix is another turn. In twenty-five prompt-injection cases,
> the guardrails held every time.
>
> I also rebuilt the agent on the Claude Agent SDK. It did about as well, but its model calls
> bypass my recording and replay, so I kept my own loop. A free eval gate runs on every pull
> request.

### 8. Trade-offs, and close (4:18)

**Show:** the README's "Known limitations".

**Say:**
> It has limits, and they're written down. A thin card pool caps how good a deck can be. And a
> false rule planted in the rules corpus would be repeated as fact, so the corpus's integrity is
> the defense. Each design decision has a record of what I chose and why. Thanks for watching:
> the code, the evals and the demo are all in the repository.

## After recording

- **Tear down the demo:** `docker compose down -v` in the fresh clone deletes the demo's data,
  and the clone can go too.
- **Link the video** from the README, beside "Try the demo", and close #154.
