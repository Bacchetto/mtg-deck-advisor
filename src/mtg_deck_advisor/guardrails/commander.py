"""The Commander deck validator: the model proposes, code decides (GRD-1). See ADR 0007.

`validate` is a pure function of a deck, the facts about its cards, and the
user's pool. It reports every violation at once, each naming the card, the
rule it breaks and why, so the agent can fix all of them in one pass and the
user can see exactly what is wrong. The rules come from section 903 of the
Comprehensive Rules, plus the format's banned list and this project's rule
that a deck is built from the user's own cards.
"""

from collections.abc import Mapping
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from mtg_deck_advisor.deck.facts import CardFacts
from mtg_deck_advisor.deck.state import DeckState

DECK_SIZE = 100

ViolationCode = Literal[
    "unknown_card",
    "commander_not_eligible",
    "banned",
    "not_legal",
    "color_identity",
    "too_many_copies",
    "not_in_pool",
    "exceeds_owned",
    "deck_size",
]


class Violation(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: ViolationCode
    # The card at fault; None for a problem with the deck as a whole.
    card: UUID | None
    message: str
    # What the violation breaks: a Comprehensive Rules number ("903.5c"),
    # "banned_list", "format_legality", "pool", or "card_data".
    rule: str


class ValidationResult(BaseModel):
    violations: list[Violation]

    @property
    def valid(self) -> bool:
        return not self.violations


def _colors(colors: frozenset[str]) -> str:
    ordered = [color for color in "WUBRG" if color in colors]
    return ", ".join(ordered) if ordered else "colorless"


def _plural(count: int, word: str, plural: str) -> str:
    return f"{count} {word if count == 1 else plural}"


def _card_checks(
    card: CardFacts, copies: int, owned: int, commander: CardFacts | None
) -> list[Violation]:
    """Every rule about one card, given how many copies the deck holds (commander included)."""
    found: list[Violation] = []

    def add(code: ViolationCode, message: str, rule: str) -> None:
        found.append(Violation(code=code, card=card.oracle_id, message=message, rule=rule))

    if card.commander_legality == "banned":
        add("banned", f"{card.name} is banned in Commander.", "banned_list")
    elif card.commander_legality == "not_legal":
        add(
            "not_legal",
            f"{card.name} is not legal in Commander (for example, a joke or digital-only card).",
            "format_legality",
        )

    if commander is not None and not card.color_identity <= commander.color_identity:
        add(
            "color_identity",
            f"{card.name}'s color identity ({_colors(card.color_identity)}) is outside "
            f"{commander.name}'s ({_colors(commander.color_identity)}).",
            "903.5c",
        )

    if card.copy_limit is not None and copies > card.copy_limit:
        add(
            "too_many_copies",
            f"The deck has {_plural(copies, 'copy', 'copies')} of {card.name}; "
            f"at most {card.copy_limit} allowed.",
            "903.5b",
        )

    if not card.is_free_basic:
        if owned == 0:
            add("not_in_pool", f"{card.name} is not in your card pool.", "pool")
        elif copies > owned:
            add(
                "exceeds_owned",
                f"The deck has {_plural(copies, 'copy', 'copies')} of {card.name}, "
                f"but your pool has {owned}.",
                "pool",
            )
    return found


def validate(
    deck: DeckState,
    facts: Mapping[UUID, CardFacts],
    pool: Mapping[UUID, int],
    *,
    check_size: bool = True,
) -> ValidationResult:
    """Every way `deck` breaks the rules, given its cards' facts and the user's pool.

    `check_size=False` leaves out the 100-card rule, for a deck still being built.
    Violations come in a stable order: the commander, then cards by name, then
    the deck's size.
    """
    violations: list[Violation] = []

    commander = facts.get(deck.commander)
    if commander is None:
        violations.append(_unknown(deck.commander))
    elif not commander.commander_eligibility.eligible:
        eligibility = commander.commander_eligibility
        violations.append(
            Violation(
                code="commander_not_eligible",
                card=commander.oracle_id,
                message=f"{commander.name} cannot be a commander: {eligibility.reason}.",
                rule=eligibility.rule,
            )
        )

    # Every card in the deck, the commander included, with its total copies.
    copies = dict(deck.cards)
    copies[deck.commander] = copies.get(deck.commander, 0) + 1

    known = [facts[card] for card in copies if card in facts]
    for card in sorted(known, key=lambda c: (c.oracle_id != deck.commander, c.name, c.oracle_id)):
        violations += _card_checks(
            card, copies[card.oracle_id], pool.get(card.oracle_id, 0), commander
        )
    violations += [
        _unknown(card) for card in sorted(copies) if card not in facts and card != deck.commander
    ]

    if check_size and deck.total != DECK_SIZE:
        violations.append(
            Violation(
                code="deck_size",
                card=None,
                message=f"The deck has {_plural(deck.total, 'card', 'cards')}; "
                f"a Commander deck has exactly {DECK_SIZE}, the commander included.",
                rule="903.5a",
            )
        )
    return ValidationResult(violations=violations)


def _unknown(card: UUID) -> Violation:
    return Violation(
        code="unknown_card",
        card=card,
        message=f"Card {card} is not in the card database.",
        rule="card_data",
    )
