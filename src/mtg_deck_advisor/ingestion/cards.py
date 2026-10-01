"""Turning Scryfall card JSON into the card records this project stores.

A record keeps only oracle-level fields: the card's rules identity, which is
the same in every printing. Scryfall's oracle_cards file attaches each card to
one "most recognisable" printing, along with that printing's prices, images and
links, and those change daily or whenever the card is reprinted. They are left
out, so the content hash changes only when the card itself does (ING-3). See
ADR 0006.
"""

import re
import unicodedata
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

# Re-exported: the hash is defined once, for every source, in sync.
from mtg_deck_advisor.ingestion.sync import content_hash as content_hash

# Scryfall layouts for objects that are not cards a deck can contain.
NON_CARD_LAYOUTS = frozenset(
    {
        "art_series",
        "double_faced_token",
        "emblem",
        "front_card",  # the front of a memorabilia insert, not a game card
        "planar",
        "scheme",
        "token",
        "vanguard",
    }
)

# The conventional order of Magic's colors: white, blue, black, red, green.
WUBRG = "WUBRG"

# Separates the faces of a multi-face card in joined text fields.
FACE_SEPARATOR = "\n//\n"

CommanderLegality = Literal["legal", "banned", "not_legal"]


class CardRecord(BaseModel):
    """One card, as stored in the `cards` table."""

    model_config = ConfigDict(frozen=True)

    oracle_id: UUID
    name: str
    name_key: str
    front_face_key: str | None
    # The same keys with punctuation ignored, for names typed without it.
    loose_name_key: str
    loose_front_face_key: str | None
    layout: str
    mana_cost: str
    cmc: float
    type_line: str
    oracle_text: str
    colors: tuple[str, ...]
    color_identity: tuple[str, ...]
    keywords: tuple[str, ...]
    produced_mana: tuple[str, ...]
    commander_legality: CommanderLegality
    game_changer: bool
    # Text, not numbers: "*", "1+*" and "2.5" all occur. None for a card with
    # no power/toughness on its front face.
    power: str | None
    toughness: str | None


def fold_name(name: str) -> str:
    """A card name reduced to the form used for lookups.

    Lowercase, accents removed, apostrophes straightened and whitespace
    collapsed, so "Lim-Dûl's  Cohort" and "lim-dul's cohort" are the same key,
    and so is the same name written with a curly apostrophe.
    """
    name = name.replace("\u2019", "'").replace("Æ", "Ae").replace("æ", "ae")
    decomposed = unicodedata.normalize("NFKD", name)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", without_accents).strip().casefold()


def loose_name(name: str) -> str:
    """A folded name with punctuation ignored, for a second, looser lookup.

    Hyphens read as spaces and other punctuation is dropped, so
    "Atraxa, Praetors' Voice" and "atraxa praetors voice" match, as do
    "Lim-Dûl's Cohort" and "lim duls cohort". The " // " between the faces of
    a multi-face card is kept. A few names collide this way (a real card and a
    joke card, such as "Lava Axe" and "Lava, Axe"); resolution's tie-break
    handles them.
    """
    faces = fold_name(name).split(" // ")
    loosened = (re.sub(r"[^\w\s]", "", face.replace("-", " ")) for face in faces)
    return " // ".join(re.sub(r"\s+", " ", face).strip() for face in loosened)


def _in_wubrg_order(colors: list[str]) -> tuple[str, ...]:
    return tuple(sorted(colors, key=WUBRG.index))


def normalise(raw: dict[str, Any]) -> CardRecord | None:
    """The stored record for one Scryfall card, or None if it is not a deck card.

    Multi-face cards (double-faced, adventure, split, ...) keep some fields only
    on their faces; those are taken from the faces when the top level lacks them.
    """
    if raw["layout"] in NON_CARD_LAYOUTS:
        return None

    faces: list[dict[str, Any]] = raw.get("card_faces", [])

    def from_faces(field: str, separator: str) -> str:
        return separator.join(face.get(field, "") for face in faces)

    if "colors" in raw:
        colors = raw["colors"]
    else:
        colors = sorted({color for face in faces for color in face.get("colors", [])})

    # Power and toughness: the top level, else the front face. A double-faced
    # card has its front face's characteristics outside the game, so one that
    # is a creature only on its back face has none.
    stats = raw if "power" in raw else (faces[0] if faces else {})

    if "oracle_text" in raw:
        oracle_text = raw["oracle_text"]
    else:
        oracle_text = from_faces("oracle_text", FACE_SEPARATOR)

    name: str = raw["name"]
    front_face = name.split(" // ")[0] if " // " in name else None
    return CardRecord(
        oracle_id=raw["oracle_id"],
        name=name,
        name_key=fold_name(name),
        front_face_key=fold_name(front_face) if front_face else None,
        loose_name_key=loose_name(name),
        loose_front_face_key=loose_name(front_face) if front_face else None,
        layout=raw["layout"],
        mana_cost=raw["mana_cost"] if "mana_cost" in raw else from_faces("mana_cost", " // "),
        cmc=raw["cmc"],
        type_line=raw["type_line"],
        oracle_text=oracle_text,
        colors=_in_wubrg_order(colors),
        color_identity=_in_wubrg_order(raw["color_identity"]),
        keywords=tuple(raw.get("keywords", [])),
        produced_mana=tuple(raw.get("produced_mana", [])),
        commander_legality=raw["legalities"]["commander"],
        game_changer=raw.get("game_changer", False),
        power=stats.get("power"),
        toughness=stats.get("toughness"),
    )
