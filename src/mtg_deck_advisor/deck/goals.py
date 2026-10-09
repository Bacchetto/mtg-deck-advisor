"""Goals a change to a deck sets, checked by code (#136).

A refine's request often has numbers in it ("cut to 37 lands", "add three
draw spells"). Stated as goals, code checks them exactly against the deck the
change started from. The agent passes them to propose_changes, which sends a
change that misses one back like an illegal deck, and the deck-task eval scores
refines by them.

Mana values and roles are over nonland cards besides the commander, as
analyze_deck counts them.
"""

from collections.abc import Sequence
from typing import Any, Literal, Self
from uuid import UUID

import psycopg
from pydantic import BaseModel, ConfigDict, Field, model_validator

from mtg_deck_advisor.deck.state import DeckState

GoalKind = Literal[
    "lands",
    "lands_change",
    "role_change",
    "role_added",
    "type_change",
    "type_cut",
    "none_at_or_above",
    "average_mana_value_lower",
]

# Kinds that count cards and take either at_least or an exact equals; with
# neither, at least one ("add more removal").
COUNTED = ("role_added", "type_cut")
AT_LEAST_ONE = ("role_change", "type_change", *COUNTED)
# Each kind's required fields.
REQUIRED: dict[str, tuple[str, ...]] = {
    "lands": ("equals",),
    "lands_change": ("equals",),
    "role_change": ("role",),
    "role_added": ("role",),
    "type_change": ("type",),
    "type_cut": ("type",),
    "none_at_or_above": ("mana_value",),
    "average_mana_value_lower": (),
}


class Goal(BaseModel):
    """One checkable goal. Which fields it needs depends on its kind."""

    model_config = ConfigDict(extra="forbid")

    kind: GoalKind = Field(
        description="lands: exactly `equals` lands. lands_change: lands change by `equals` "
        "(-2 for two fewer). role_change: cards with `role` up by `at_least` overall, optionally "
        "only those at or below `max_mana_value`. role_added: the cards added that have `role` "
        "(and are at or below `max_mana_value`, if given), exactly `equals` or at least "
        "`at_least`. type_change: cards with `type` in their type line up by `at_least`. "
        "type_cut: cards of `type` cut, exactly `equals` or at least `at_least`. "
        "none_at_or_above: no nonland card at `mana_value` or more. "
        "average_mana_value_lower: a lower average mana value."
    )
    equals: int | None = Field(
        default=None,
        description="An exact count: required for lands and lands_change, optional for "
        "role_added and type_cut.",
    )
    at_least: int | None = Field(
        default=None,
        description="A minimum count for role_change, role_added, type_change and type_cut; "
        "leave it out to mean at least 1.",
    )
    role: str | None = Field(
        default=None,
        description="For role_change and role_added: a role tag, such as 'removal'.",
    )
    type: str | None = Field(
        default=None,
        description="For type_change and type_cut: a card type, such as 'Creature'.",
    )
    mana_value: float | None = Field(default=None, description="For none_at_or_above.")
    max_mana_value: float | None = Field(
        default=None,
        description="For role_change and role_added: only cards at or below this mana value.",
    )

    @model_validator(mode="after")
    def _has_its_fields(self) -> Self:
        missing = [name for name in REQUIRED[self.kind] if getattr(self, name) is None]
        if missing:
            raise ValueError(f"a {self.kind} goal needs {', '.join(missing)}")
        if self.kind in COUNTED and self.equals is not None and self.at_least is not None:
            raise ValueError(f"a {self.kind} goal takes equals or at_least, not both")
        if self.kind in AT_LEAST_ONE and self.equals is None and self.at_least is None:
            # The live agent leaves the count out of "add more removal" (#136).
            self.at_least = 1
        return self

    def _how_many(self, noun: str) -> str:
        if self.equals is not None:
            return f"exactly {self.equals} {noun}{'' if self.equals == 1 else 's'}"
        return f"{self.at_least} or more {noun}s"

    def describe(self) -> str:
        match self.kind:
            case "lands":
                return f"exactly {self.equals} lands"
            case "lands_change":
                return f"lands change by {self.equals:+d}"
            case "role_change":
                cheap = (
                    f" at mana value {self.max_mana_value:g} or less" if self.max_mana_value else ""
                )
                return f"{self.role}{cheap} up by {self.at_least} or more"
            case "role_added":
                cheap = (
                    f" at mana value {self.max_mana_value:g} or less" if self.max_mana_value else ""
                )
                return f"{self._how_many(f'{self.role} card')}{cheap} added"
            case "type_change":
                return f"{self.type} cards up by {self.at_least} or more"
            case "type_cut":
                return f"{self._how_many(f'{self.type} card')} cut"
            case "none_at_or_above":
                return f"no nonland card at mana value {self.mana_value:g} or more"
            case _:
                return "a lower average mana value"


def _enough(goal: Goal, count: int) -> bool:
    """A counted goal: exactly `equals`, or at least `at_least`."""
    return count == goal.equals if goal.equals is not None else count >= (goal.at_least or 0)


def _facts(
    conn: psycopg.Connection, ids: Sequence[UUID]
) -> dict[UUID, tuple[float, str, list[str]]]:
    """Each card's mana value, type line and roles."""
    rows = conn.execute(
        "SELECT c.oracle_id, c.cmc, c.type_line, coalesce(r.roles, '{}') FROM cards c "
        "LEFT JOIN card_roles r USING (oracle_id) WHERE c.oracle_id = ANY(%s)",
        (list(ids),),
    ).fetchall()
    return {row[0]: (float(row[1] or 0), row[2], list(row[3])) for row in rows}


def check_goals(
    conn: psycopg.Connection, goals: Sequence[Goal], start: DeckState, final: DeckState
) -> list[dict[str, Any]]:
    """Each goal, whether the changed deck meets it, and the value that decided it."""
    facts = _facts(conn, list({*start.cards, *final.cards}))

    def is_land(card: UUID) -> bool:
        return "Land" in facts[card][1].split("—")[0]

    def has_type(card: UUID, name: str) -> bool:
        return name in facts[card][1].split("—")[0]

    def counted(goal: Goal, card: UUID) -> bool:
        """Whether a card counts toward a role_change or type_change goal."""
        if goal.kind == "type_change":
            return has_type(card, goal.type or "")
        cheap = goal.max_mana_value is None or facts[card][0] <= goal.max_mana_value
        return not is_land(card) and goal.role in facts[card][2] and cheap

    def count(deck: DeckState, goal: Goal | None = None) -> int:
        """Lands, or with a goal, the cards that count toward it."""
        return sum(
            n for card, n in deck.cards.items() if (counted(goal, card) if goal else is_land(card))
        )

    def average(deck: DeckState) -> float:
        values = [facts[c][0] for c, n in deck.cards.items() if not is_land(c) for _ in range(n)]
        return sum(values) / len(values) if values else 0.0

    results = []
    for goal in goals:
        met: bool
        shown: str
        if goal.kind == "lands":
            actual = count(final)
            met, shown = actual == goal.equals, str(actual)
        elif goal.kind == "lands_change":
            actual = count(final) - count(start)
            met, shown = actual == goal.equals, f"{actual:+d}"
        elif goal.kind in ("role_change", "type_change"):
            actual = count(final, goal) - count(start, goal)
            met, shown = actual >= (goal.at_least or 0), f"{actual:+d}"
        elif goal.kind == "role_added":
            added = sum(
                max(final.count(card) - start.count(card), 0)
                for card in final.cards
                if counted(goal, card)
            )
            met, shown = _enough(goal, added), str(added)
        elif goal.kind == "type_cut":
            cut = sum(
                max(start.count(card) - final.count(card), 0)
                for card in start.cards
                if has_type(card, goal.type or "")
            )
            met, shown = _enough(goal, cut), str(cut)
        elif goal.kind == "none_at_or_above":
            ceiling = goal.mana_value or 0.0
            above = sum(
                n for c, n in final.cards.items() if not is_land(c) and facts[c][0] >= ceiling
            )
            met, shown = above == 0, f"{above} at or above"
        else:
            before, after = average(start), average(final)
            met, shown = after < before, f"{before:.2f} → {after:.2f}"
        results.append({"goal": goal.describe(), "met": met, "actual": shown})
    return results
