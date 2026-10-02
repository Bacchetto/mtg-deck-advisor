"""Tagging each card with the roles it plays in a Commander deck (ramp, removal, ...).

The roles feed deck composition analysis: "this deck has 4 ramp cards; most
Commander decks want about 10". Cards are tagged in batches, through ModelClient,
against a fixed taxonomy whose definitions are in the prompt. The system prompt
is identical for every batch, so prompt caching reuses it.

Changing a definition or the prompt changes what a role means, so it must bump
PROMPT_VERSION. Stored tags record the version that produced them, so stale
tags can be found and redone.
"""

from collections.abc import Iterable, Sequence
from typing import Literal
from uuid import UUID

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from mtg_deck_advisor.llm.client import ModelClient
from mtg_deck_advisor.llm.errors import InvalidOutputError
from mtg_deck_advisor.llm.types import Message, ModelRequest

PROMPT_VERSION = "roles-v2"

Role = Literal[
    "ramp",
    "card_draw",
    "removal",
    "board_wipe",
    "counterspell",
    "tutor",
    "recursion",
    "protection",
    "token_maker",
    "finisher",
    "mana_fixing",
    "land",
    "mill",
    "self_mill",
]

# The taxonomy agreed with the owner (2026-10-02; mill and self_mill added in the
# owner's review of the gold labels): each role's meaning and its edge-case
# rules. These definitions are what every model, and the human-labelled gold
# sample, follow.
ROLE_DEFINITIONS: dict[Role, str] = {
    "ramp": (
        "gives extra mana beyond the normal land drop: mana rocks, mana creatures, putting "
        "lands onto the battlefield, cost reducers. Treasure-makers count; one-shot rituals "
        "don't."
    ),
    "card_draw": (
        "gives extra cards: drawing, or exiling cards you may play. Looting that discards as "
        "much as it draws doesn't count, nor does a cantrip that only replaces itself."
    ),
    "removal": (
        "deals with one opposing threat: destroy, exile, sacrifice, damage or bounce aimed at "
        "a single permanent."
    ),
    "board_wipe": (
        "deals with many permanents at once: destroy all, mass damage, mass bounce or exile."
    ),
    "counterspell": "counters spells or abilities.",
    "tutor": (
        "searches your library for a specific non-land card. Searching for lands is ramp or "
        "mana_fixing."
    ),
    "recursion": "returns cards from a graveyard, to hand or to the battlefield.",
    "protection": (
        "protects your own permanents or yourself: hexproof, indestructible, shroud, phasing, "
        "shield counters, countering spells that target them."
    ),
    "token_maker": (
        "creates creature tokens. Treasure and other non-creature tokens don't count (Treasure "
        "is ramp)."
    ),
    "finisher": (
        "ends games: mass pumps for your team, alternate win conditions, large evasive threats."
    ),
    "mana_fixing": (
        "produces or finds mana of more than one color: dual lands, any-color mana, searching "
        "for basic lands to fix colors."
    ),
    "land": "is a land card.",
    "mill": (
        "puts cards from an opponent's library into their graveyard: milling as a win "
        "condition or disruption."
    ),
    "self_mill": (
        "puts cards from your own library into your graveyard, to fuel graveyard strategies: "
        "milling yourself, surveil, putting revealed cards into your graveyard."
    ),
}

SYSTEM_PROMPT = "\n".join(
    [
        "You classify Magic: The Gathering cards by the roles they play in a Commander deck.",
        f"Taxonomy version: {PROMPT_VERSION}.",
        "",
        "Roles (use exactly these names):",
        *(f"- {role}: {definition}" for role, definition in ROLE_DEFINITIONS.items()),
        "",
        "A card can have several roles, or none (for example a vanilla creature).",
        "Judge each card by its own text, as a Commander player would.",
        "For every card in the list, give its number, its roles, and a one-sentence reason.",
    ]
)

MAX_TOKENS_PER_CARD = 120


class CardToTag(BaseModel):
    model_config = ConfigDict(frozen=True)

    oracle_id: UUID
    name: str
    type_line: str
    mana_cost: str
    oracle_text: str


class TaggedCard(BaseModel):
    """The model's answer for one card, by its number in the list."""

    index: int
    roles: list[Role]
    reason: str


class RoleTagging(BaseModel):
    """The model's answer for a batch of cards."""

    cards: list[TaggedCard]


class CardRoles(BaseModel):
    """A card's stored roles, with what produced them."""

    model_config = ConfigDict(frozen=True)

    oracle_id: UUID
    roles: list[Role]
    reason: str
    # The card's content hash when tagged: a different hash means the card has
    # changed since, so its tags may be stale.
    content_hash: str
    prompt_version: str
    model: str = Field(description="The model that answered.")


def build_request(cards: Sequence[CardToTag]) -> ModelRequest:
    listing = "\n".join(
        f"{number}. {card.name} | {card.mana_cost} | {card.type_line} | "
        f"{card.oracle_text.replace(chr(10), ' / ')}"
        for number, card in enumerate(cards, start=1)
    )
    return ModelRequest(
        purpose="role_tagging",
        system=SYSTEM_PROMPT,
        messages=(Message(role="user", content=f"Classify these cards:\n{listing}"),),
        max_tokens=max(1000, MAX_TOKENS_PER_CARD * len(cards)),
        effort="low",
    )


def tag_cards(client: ModelClient, cards: Sequence[CardToTag]) -> dict[UUID, TaggedCard]:
    """Roles for a batch of cards, from one model call, keyed by card.

    The answer must cover every card exactly once; anything else is rejected
    rather than guessed at.
    """
    result = client.generate_structured(build_request(cards), RoleTagging)
    indexes = sorted(tagged.index for tagged in result.value.cards)
    expected = list(range(1, len(cards) + 1))
    if indexes != expected:
        raise InvalidOutputError(
            f"role tagging answered for cards {indexes}; expected {', '.join(map(str, expected))}"
        )
    return {cards[tagged.index - 1].oracle_id: tagged for tagged in result.value.cards}


def save_roles(conn: psycopg.Connection, roles: Iterable[CardRoles]) -> None:
    """Store tags, replacing any earlier tags for the same cards."""
    with conn.transaction():
        conn.cursor().executemany(
            """
            INSERT INTO card_roles (oracle_id, roles, reason, content_hash, prompt_version, model)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (oracle_id) DO UPDATE SET
                roles = EXCLUDED.roles,
                reason = EXCLUDED.reason,
                content_hash = EXCLUDED.content_hash,
                prompt_version = EXCLUDED.prompt_version,
                model = EXCLUDED.model,
                tagged_at = now()
            """,
            [
                (r.oracle_id, list(r.roles), r.reason, r.content_hash, r.prompt_version, r.model)
                for r in roles
            ],
        )


def load_roles(conn: psycopg.Connection, oracle_ids: Iterable[UUID]) -> dict[UUID, CardRoles]:
    rows = conn.execute(
        """
        SELECT oracle_id, roles, reason, content_hash, prompt_version, model
        FROM card_roles WHERE oracle_id = ANY(%s)
        """,
        (list(oracle_ids),),
    )
    return {
        row[0]: CardRoles(
            oracle_id=row[0],
            roles=row[1],
            reason=row[2],
            content_hash=row[3],
            prompt_version=row[4],
            model=row[5],
        )
        for row in rows
    }
