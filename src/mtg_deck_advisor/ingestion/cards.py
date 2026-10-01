"""Turning Scryfall card JSON into the card records this project stores.

A record keeps only oracle-level fields: the card's rules identity, which is
the same in every printing. Scryfall's oracle_cards file attaches each card to
one "most recognisable" printing, along with that printing's prices, images and
links, and those change daily or whenever the card is reprinted. They are left
out, so the content hash changes only when the card itself does (ING-3). See
ADR 0006.
"""

import hashlib
import json
import re
import unicodedata
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

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

    if "oracle_text" in raw:
        oracle_text = raw["oracle_text"]
    else:
        oracle_text = from_faces("oracle_text", FACE_SEPARATOR)

    name: str = raw["name"]
    return CardRecord(
        oracle_id=raw["oracle_id"],
        name=name,
        name_key=fold_name(name),
        front_face_key=fold_name(name.split(" // ")[0]) if " // " in name else None,
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
    )


def content_hash(record: CardRecord) -> str:
    """SHA-256 of the record's canonical JSON: same card data, same hash."""
    canonical = json.dumps(
        record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
