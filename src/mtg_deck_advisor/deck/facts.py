"""The facts about a card that the Commander deck rules need, and the helpers that derive them.

Each helper implements one piece of a rule from the Comprehensive Rules
(section 903, the Commander variant), using the card's stored oracle data. A
multi-face card is judged on its front face, because outside the game a card
has its front face's characteristics. See ADR 0007.
"""

import re
from collections.abc import Iterable
from uuid import UUID

import psycopg
from pydantic import BaseModel, ConfigDict

from mtg_deck_advisor.ingestion.cards import CommanderLegality

# The basic lands every player is assumed to have in any number. Other basic
# lands (Snow-Covered basics, Wastes) may appear in any number too, but must
# come from the user's pool.
FREE_BASICS = frozenset({"Plains", "Island", "Swamp", "Mountain", "Forest"})

NUMBER_WORDS = {
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


class Eligibility(BaseModel):
    """Whether a card can be a commander, and the rule that decides it."""

    model_config = ConfigDict(frozen=True)

    eligible: bool
    rule: str
    reason: str


class CardFacts(BaseModel):
    """What the deck rules need to know about one card."""

    model_config = ConfigDict(frozen=True)

    oracle_id: UUID
    name: str
    color_identity: frozenset[str]
    commander_legality: CommanderLegality
    is_basic_land: bool
    is_free_basic: bool
    # How many copies a deck may hold: 1, a number from the card's own text,
    # or None for any number.
    copy_limit: int | None
    commander_eligibility: Eligibility

    @classmethod
    def from_card(
        cls,
        *,
        oracle_id: UUID,
        name: str,
        type_line: str,
        oracle_text: str,
        color_identity: Iterable[str],
        commander_legality: CommanderLegality,
        power: str | None,
        toughness: str | None,
    ) -> "CardFacts":
        return cls(
            oracle_id=oracle_id,
            name=name,
            color_identity=frozenset(color_identity),
            commander_legality=commander_legality,
            is_basic_land=is_basic_land(type_line),
            is_free_basic=is_free_basic(name),
            copy_limit=copy_limit(name, oracle_text, type_line),
            commander_eligibility=commander_eligibility(
                name, type_line, oracle_text, power, toughness
            ),
        )


def _front_face(text: str) -> str:
    return text.split(" // ")[0]


def _types(type_line: str) -> tuple[set[str], set[str]]:
    """The front face's supertypes and card types, and its subtypes."""
    before, _, after = _front_face(type_line).partition(" — ")
    return set(before.split()), set(after.split())


def is_basic_land(type_line: str) -> bool:
    """A land with the Basic supertype.

    Basic land *types* don't count: Tundra ("Land — Plains Island") is not a basic land.
    """
    types, _ = _types(type_line)
    return {"Basic", "Land"} <= types


def is_free_basic(name: str) -> bool:
    return name in FREE_BASICS


def copy_limit(name: str, oracle_text: str, type_line: str) -> int | None:
    """How many copies of a card a deck may hold (903.5b).

    Basic lands are unlimited. Otherwise one, unless the card's own text says
    "A deck can have any number of cards named <this card>" or "up to <n>".
    Only a statement about the card itself counts.
    """
    if is_basic_land(type_line):
        return None
    for card_name in {name, _front_face(name)}:
        named = re.escape(card_name)
        if re.search(rf"A deck can have any number of cards named {named}\.", oracle_text):
            return None
        if found := re.search(rf"A deck can have up to (\w+) cards named {named}\.", oracle_text):
            word = found[1]
            return int(word) if word.isdigit() else NUMBER_WORDS[word]
    return 1


def commander_eligibility(
    name: str, type_line: str, oracle_text: str, power: str | None, toughness: str | None
) -> Eligibility:
    """Whether a card can be a commander (903.3, 903.3a), judged on its front face."""
    for card_name in {name, _front_face(name)}:
        if f"{card_name} can be your commander" in oracle_text:
            return Eligibility(
                eligible=True, rule="903.3a", reason="its text says it can be your commander"
            )

    types, subtypes = _types(type_line)
    if "Legendary" not in types:
        return Eligibility(eligible=False, rule="903.3", reason="it is not legendary")
    if "Creature" in types:
        return Eligibility(eligible=True, rule="903.3", reason="a legendary creature")
    if "Vehicle" in subtypes:
        return Eligibility(eligible=True, rule="903.3", reason="a legendary Vehicle")
    if "Spacecraft" in subtypes:
        if power is not None and toughness is not None:
            return Eligibility(
                eligible=True, rule="903.3", reason="a legendary Spacecraft with power/toughness"
            )
        return Eligibility(
            eligible=False,
            rule="903.3",
            reason="a Spacecraft can be a commander only if it has power/toughness",
        )
    return Eligibility(
        eligible=False,
        rule="903.3",
        reason="it is not a legendary creature, Vehicle or Spacecraft",
    )


def load_card_facts(conn: psycopg.Connection, oracle_ids: Iterable[UUID]) -> dict[UUID, CardFacts]:
    """Facts for each requested card that exists and has not been removed."""
    rows = conn.execute(
        """
        SELECT oracle_id, name, type_line, oracle_text, color_identity,
               commander_legality, power, toughness
        FROM cards
        WHERE removed_at IS NULL AND oracle_id = ANY(%s)
        """,
        (list(oracle_ids),),
    )
    return {
        row[0]: CardFacts.from_card(
            oracle_id=row[0],
            name=row[1],
            type_line=row[2],
            oracle_text=row[3],
            color_identity=row[4],
            commander_legality=row[5],
            power=row[6],
            toughness=row[7],
        )
        for row in rows
    }
