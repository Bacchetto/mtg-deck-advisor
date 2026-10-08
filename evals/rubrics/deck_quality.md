# Rubric: how good is a Commander deck?

Used to grade the decks the agent builds in the deck-build task set (`evals/datasets/deck_tasks.jsonl`, #122). Opus 5.5 grades each deck with this rubric, and the owner grades a sample by hand with the same rubric, blind to the model's scores, so the two can be compared (EVL-3).

**Legality isn't graded.** Code has already checked it, and only legal decks are graded.

**The pool isn't graded either.** A deck is built from the cards the user owns, so a weak spot may be all the pool allows. The grader doesn't see the pool, so it judges the deck as it stands, as a casual Commander deck.

## What the grader is given

- **The request:** a named commander, a theme, a change to a saved deck, or none.
- **The deck:** the commander first, then each card with its mana cost, type line and rules text.
- **The deck's counts:** lands, and cards by mana value.

## Criteria

Each is scored from 1 to 5. The anchors describe 1, 3 and 5 (fit to the request also describes 2); the other scores fall between them.

### 1. Plan coherence
Does the deck do one recognisable thing, and do its cards serve that?
- **1:** a pile of cards with no plan. The commander is incidental.
- **3:** a clear plan, but a fifth or more of the cards don't serve it.
- **5:** almost every card serves a plan built around the commander.

### 2. Fit to the request
Does the deck do what was asked? This is skipped when there was no request.
- **1:** ignores the request: the wrong commander, or no sign of the theme or change.
- **2:** follows the request in name only. For example, it has the named commander but isn't built around it, or the theme is a handful of cards.
- **3:** clearly follows the request, but it's only part of the deck: the named commander leads a plan of its own, or the theme is about a third of the cards.
- **5:** built around the request. For a change to a saved deck, it makes the change asked for, and only that.

**For a change to a saved deck,** the deck text ends with the change: the starting deck's lands and curve, and the cards cut and added. Score fit on the change: do the cuts and additions do what was asked, as fully as the pool allows? Don't mark fit down for flaws the starting deck already had that the request didn't ask to fix. The other criteria still judge the deck as it now is.

### 3. Mana base
Can the deck cast its spells?
- **1:** clearly too few or too many lands for its curve (fewer than 30 or more than 45 in a normal deck), or colours it can't produce reliably.
- **3:** a workable land count, with some colour strain or several lands that enter tapped and slow it down.
- **5:** a land count that suits the curve (usually 35-38, or fewer with lots of ramp), and dependable colours.

### 4. Ramp
Does it accelerate its mana enough for its curve?
- **1:** none, or one or two pieces in a deck with an expensive curve.
- **3:** about 5-7 pieces, or fewer if the deck has a cheap curve.
- **5:** about 8-12 pieces that suit the deck, such as mana rocks, land search and mana creatures, or a curve low enough not to need them.

### 5. Card draw
Does it keep cards flowing?
- **1:** none.
- **3:** a few one-shot draws, or a single engine.
- **5:** about 8 or more sources, including repeatable ones, that suit the plan.

### 6. Interaction
Can it deal with the opponents' threats?
- **1:** none.
- **3:** a few pieces of removal, aimed narrowly or at one kind of permanent.
- **5:** about 8 or more flexible answers (removal, counterspells, protection), including a way to reset the board.

## How to judge

1. **Judge the deck as given.** Don't penalise it for cards it couldn't have; the grader doesn't know the pool.
2. **Count the cards.** Base each criterion on the card texts and the counts given, not on general impressions.
3. **Use the whole scale.** A 5 is a deck you'd be glad to sit down with; a 1 is one you wouldn't.

## Output

- **A score for each criterion,** 1 to 5. Fit to the request is null when there was no request.
- **A reason for each criterion:** one sentence, naming cards or counts.
- **A summary:** one or two sentences on the deck as a whole.

## Scoring

- **A deck's quality:** the mean of its criterion scores.
- **Agreement with the owner, per criterion and overall:**
  - **exact:** the same score
  - **within one point**
  - **weighted kappa:** quadratic weights, so a larger disagreement counts for more
