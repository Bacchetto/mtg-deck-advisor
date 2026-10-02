# 0007 - A pure Commander validator that reports every violation and cites its rule

**Status:** Accepted, 2026-10-02
**Applies to:** `mtg_deck_advisor.guardrails.commander`, `mtg_deck_advisor.deck`

## Context

GRD-1 requires that deterministic validation check every model proposal before it takes effect: "the model proposes; code decides". AGT-3 requires that application state be owned by code. From Milestone 5, an agent will propose deck changes, and something has to decide, reliably and explainably, whether the result is a legal Commander deck built from the user's cards.

The rules are precise and stored in the project's own database: section 903 of the Comprehensive Rules (2026-09-25 edition). Some are easy to get subtly wrong:
- Rule 903.3 recently began allowing legendary Vehicles, and Spacecraft with power/toughness, as commanders.
- A double-faced card is judged on its front face.
- 13 cards override the singleton rule in their own text.

The project adds its own rules: cards come from the user's pool, up to the owned count, and only the five basic lands are free.

## Decision

- **The deck is an immutable value** (`DeckState`). Every edit returns a new deck, so a proposal can be applied to a copy, validated, and discarded. There's no undo to get wrong, and no half-edited deck.
- **The validator is a pure function:** `validate(deck, facts, pool)`. It takes no database connection and no clock. Card facts are loaded separately (`load_card_facts`), so the rules are testable without a database and run in well under a millisecond.
- **It reports every violation,** not just the first, in a stable order. Each violation names the card, gives a plain-language message, and cites what it breaks: a Comprehensive Rules number (`903.5c`), `banned_list`, `format_legality`, `pool`, or `card_data`. The agent can fix every problem in one pass, the user sees exactly what's wrong, and later the citation can link to the stored rule text.
- **Each rule helper implements one numbered rule,** on the card's front face:
  - 903.3: legendary creature, Vehicle, or Spacecraft with power/toughness
  - 903.3a: "this card can be your commander"
  - 903.5a: exactly 100 cards
  - 903.5b: singleton, with basics unlimited and limits read from the card's *own* text ("any number", "up to seven")
  - 903.5c: color identity
- **Cards outside the format count, not only banned ones:** joke and digital-only cards (`not_legal`) are reported separately from banned ones.
- **Building and finishing are separate:** `check_size=False` leaves out the 100-card rule while a deck is being built.

## Alternatives

**Return only the first violation**, or a boolean. Simpler, but an agent fixing one problem at a time needs many round trips, and a bare "invalid" can't be explained or tested precisely.

**Hard-code the singleton exceptions.** That's a list of 13 names to maintain by hand. Reading them from card text picks up a newly printed exception at the next ingestion.

**Validate inside the deck model**, so an illegal deck can't exist. That's appealing, but a deck is illegal for most of its construction (it has fewer than 100 cards), and the agent needs to *see* an illegal state to fix it. The model enforces only its own structure (positive counts).

**Let the database enforce the rules with constraints.** Some rules could be constraints, but the messages would be database errors, not explanations citing rules, and the pool rule depends on per-user data.

## Consequences

- **Real data:**
  - a real 100-card Atraxa deck from the full card table validates clean in 0.3 ms
  - breaking each rule alone gives exactly that violation
  - every rule number the validator cites (903.3, 903.5a, 903.5b, 903.5c) exists in the stored rules
- **Property tests** (Hypothesis) check rules that must hold for any input: a card is reported off-color exactly when it is, and swapping a legal card for another keeps a deck legal.
- **The pool rule is this project's, not the game's.** It's labelled `pool`, so it's never mistaken for a Comprehensive Rules requirement.
- **Out of scope** (decided in planning): partner and background commanders, companions, and the Commander brackets / "game changer" guidance. A deck with two commanders is outside what `DeckState` can represent.
