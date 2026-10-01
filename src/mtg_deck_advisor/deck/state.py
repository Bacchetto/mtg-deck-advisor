"""The deck being built: a commander and the other cards, owned by code (AGT-3).

A DeckState never changes. Every edit returns a new deck, so a proposed change
can be applied to a copy, validated, and thrown away if rejected, without any
risk of the real deck being left half-edited. The model checks only its own
structure (positive counts); whether a deck is legal is the validator's job.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Self
from uuid import UUID


@dataclass(frozen=True, slots=True)
class DeckState:
    commander: UUID
    # (card, count) pairs sorted by card ID: one canonical form per deck, so
    # equal decks compare and hash equal whatever order cards were added in.
    # Use `cards` to read them.
    _entries: tuple[tuple[UUID, int], ...] = ()

    @classmethod
    def new(cls, commander: UUID, cards: Mapping[UUID, int] | None = None) -> Self:
        cards = cards or {}
        for card, count in cards.items():
            _require_positive(card, count)
        return cls(commander, tuple(sorted(cards.items())))

    @property
    def cards(self) -> Mapping[UUID, int]:
        """The cards besides the commander, with their counts. Read-only."""
        return MappingProxyType(dict(self._entries))

    @property
    def total(self) -> int:
        """The number of cards in the deck, the commander included."""
        return 1 + sum(count for _, count in self._entries)

    def count(self, card: UUID) -> int:
        return dict(self._entries).get(card, 0)

    def with_card(self, card: UUID, count: int = 1) -> Self:
        _require_positive(card, count)
        cards = dict(self._entries)
        cards[card] = cards.get(card, 0) + count
        return type(self).new(self.commander, cards)

    def without_card(self, card: UUID, count: int = 1) -> Self:
        _require_positive(card, count)
        cards = dict(self._entries)
        held = cards.get(card, 0)
        if count > held:
            raise ValueError(f"cannot remove {count} of {card}: the deck holds {held}")
        if count == held:
            del cards[card]
        else:
            cards[card] = held - count
        return type(self).new(self.commander, cards)

    def with_commander(self, commander: UUID) -> Self:
        return type(self)(commander, self._entries)

    def to_dict(self) -> dict[str, Any]:
        """A JSON-ready form: {"commander": id, "cards": {id: count}}."""
        return {
            "commander": str(self.commander),
            "cards": {str(card): count for card, count in self._entries},
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        return cls.new(
            UUID(data["commander"]),
            {UUID(card): int(count) for card, count in data["cards"].items()},
        )


def _require_positive(card: UUID, count: int) -> None:
    if count < 1:
        raise ValueError(f"count for {card} must be at least 1, not {count}")
