"""Normalising Scryfall card JSON. The fixtures are trimmed from real oracle_cards entries."""

import copy
import re
from typing import Any

import pytest

from mtg_deck_advisor.ingestion.cards import content_hash, fold_name, loose_name, normalise

SOL_RING: dict[str, Any] = {
    "object": "card",
    "oracle_id": "6ad8011d-3471-4369-9d68-b264cc027487",
    "name": "Sol Ring",
    "layout": "normal",
    "mana_cost": "{1}",
    "cmc": 1.0,
    "type_line": "Artifact",
    "oracle_text": "{T}: Add {C}{C}.",
    "colors": [],
    "color_identity": [],
    "keywords": [],
    "produced_mana": ["C"],
    "legalities": {"commander": "legal", "vintage": "restricted"},
    "game_changer": True,
    # Printing-specific and daily-changing fields, which must not affect the hash.
    "set": "cmr",
    "set_type": "commander",
    "scryfall_uri": "https://scryfall.com/card/cmr/472/sol-ring?utm_source=api",
    "prices": {"usd": "1.50"},
    "image_uris": {"normal": "https://cards.scryfall.io/normal/front/sol-ring.jpg"},
    "edhrec_rank": 1,
}

TRANSFORM: dict[str, Any] = {
    "object": "card",
    "oracle_id": "0b2a1e3a-6f1c-4b2a-9d43-6c5e3b8f2a10",
    "name": "Delver of Secrets // Insectile Aberration",
    "layout": "transform",
    "cmc": 1.0,
    "type_line": "Creature — Human Wizard // Creature — Human Insect",
    "color_identity": ["U"],
    "keywords": ["Transform", "Flying"],
    "legalities": {"commander": "legal"},
    "game_changer": False,
    "card_faces": [
        {
            "name": "Delver of Secrets",
            "mana_cost": "{U}",
            "type_line": "Creature — Human Wizard",
            "oracle_text": "At the beginning of your upkeep, look at the top card of your library.",
            "colors": ["U"],
            "power": "1",
            "toughness": "1",
        },
        {
            "name": "Insectile Aberration",
            "mana_cost": "",
            "type_line": "Creature — Human Insect",
            "oracle_text": "Flying",
            "colors": ["U"],
            "power": "3",
            "toughness": "2",
        },
    ],
}

ADVENTURE: dict[str, Any] = {
    "object": "card",
    "oracle_id": "9b3d6c2e-1a4f-4f0e-8b7a-2c1d0e9f8a7b",
    "name": "Bonecrusher Giant // Stomp",
    "layout": "adventure",
    "mana_cost": "{2}{R} // {1}{R}",
    "cmc": 3.0,
    "type_line": "Creature — Giant // Instant — Adventure",
    "colors": ["R"],
    "color_identity": ["R"],
    "keywords": [],
    "power": "4",
    "toughness": "3",
    "legalities": {"commander": "legal"},
    "game_changer": False,
    "card_faces": [
        {
            "name": "Bonecrusher Giant",
            "mana_cost": "{2}{R}",
            "type_line": "Creature — Giant",
            "oracle_text": "Whenever Bonecrusher Giant becomes the target of a spell, "
            "Bonecrusher Giant deals 2 damage to that spell's controller.",
        },
        {
            "name": "Stomp",
            "mana_cost": "{1}{R}",
            "type_line": "Instant — Adventure",
            "oracle_text": "Damage can't be prevented this turn. "
            "Stomp deals 2 damage to any target.",
        },
    ],
}


def card(base: dict[str, Any], **changes: Any) -> dict[str, Any]:
    result = copy.deepcopy(base)
    result.update(changes)
    return result


# --- fold_name -------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "folded"),
    [
        ("Sol Ring", "sol ring"),
        ("  SOL   ring ", "sol ring"),
        ("Lim-Dûl's Cohort", "lim-dul's cohort"),
        ("Círdan the Shipwright", "cirdan the shipwright"),
        # A curly apostrophe, as pasted from web pages.
        ("Lim-Dûl\u2019s Cohort", "lim-dul's cohort"),
        ("Æther Vial", "aether vial"),
    ],
)
def test_fold_name_ignores_case_accents_spacing_and_apostrophe_style(
    name: str, folded: str
) -> None:
    assert fold_name(name) == folded


@pytest.mark.parametrize(
    ("name", "loose"),
    [
        ("Atraxa, Praetors' Voice", "atraxa praetors voice"),
        ("Lim-Dûl's Cohort", "lim duls cohort"),  # a hyphen reads as a space
        (
            "Kytheon, Hero of Akros // Gideon, Battle-Forged",
            "kytheon hero of akros // gideon battle forged",
        ),
        ("Fire // Ice", "fire // ice"),  # the face separator is kept
        ("B.F.M. (Big Furry Monster)", "bfm big furry monster"),
        ("70,000 Light-Years from Home", "70000 light years from home"),
        ("Sol Ring", "sol ring"),
    ],
)
def test_loose_name_also_ignores_punctuation(name: str, loose: str) -> None:
    assert loose_name(name) == loose


# --- normalise -------------------------------------------------------------


def test_a_single_faced_card_keeps_its_oracle_fields() -> None:
    record = normalise(SOL_RING)

    assert record is not None
    assert str(record.oracle_id) == "6ad8011d-3471-4369-9d68-b264cc027487"
    assert record.name == "Sol Ring"
    assert record.name_key == "sol ring"
    assert record.front_face_key is None
    assert record.loose_name_key == "sol ring"
    assert record.loose_front_face_key is None
    assert record.mana_cost == "{1}"
    assert record.cmc == 1.0
    assert record.type_line == "Artifact"
    assert record.oracle_text == "{T}: Add {C}{C}."
    assert record.color_identity == ()
    assert record.produced_mana == ("C",)
    assert record.commander_legality == "legal"
    assert record.game_changer is True


@pytest.mark.parametrize(
    "layout",
    ["token", "double_faced_token", "art_series", "emblem", "planar", "scheme", "vanguard"],
)
def test_things_that_are_not_cards_are_dropped(layout: str) -> None:
    assert normalise(card(SOL_RING, layout=layout)) is None


def test_memorabilia_front_cards_are_dropped() -> None:
    assert normalise(card(SOL_RING, layout="front_card")) is None


def test_a_double_faced_card_takes_text_colors_and_cost_from_its_faces() -> None:
    record = normalise(TRANSFORM)

    assert record is not None
    assert record.oracle_text == (
        "At the beginning of your upkeep, look at the top card of your library.\n//\nFlying"
    )
    assert record.mana_cost == "{U} // "
    assert record.colors == ("U",)
    assert record.front_face_key == "delver of secrets"
    assert record.name_key == "delver of secrets // insectile aberration"
    assert record.loose_front_face_key == "delver of secrets"


def test_an_adventure_card_takes_text_from_its_faces_but_keeps_its_own_cost() -> None:
    record = normalise(ADVENTURE)

    assert record is not None
    assert record.mana_cost == "{2}{R} // {1}{R}"
    assert record.oracle_text.startswith("Whenever Bonecrusher Giant becomes the target")
    assert record.oracle_text.endswith("Stomp deals 2 damage to any target.")
    assert "\n//\n" in record.oracle_text
    assert record.front_face_key == "bonecrusher giant"


def test_banned_and_not_legal_cards_are_kept_with_their_legality() -> None:
    banned = normalise(card(SOL_RING, legalities={"commander": "banned"}))
    joke = normalise(card(SOL_RING, legalities={"commander": "not_legal"}))

    assert banned is not None and banned.commander_legality == "banned"
    assert joke is not None and joke.commander_legality == "not_legal"


def test_color_identity_is_put_in_wubrg_order() -> None:
    # Scryfall sorts color identity alphabetically.
    record = normalise(card(SOL_RING, color_identity=["B", "G", "R", "U", "W"]))

    assert record is not None
    assert record.color_identity == ("W", "U", "B", "R", "G")


def test_optional_fields_default_when_absent() -> None:
    raw = card(SOL_RING)
    for key in ("produced_mana", "keywords", "game_changer"):
        del raw[key]

    record = normalise(raw)

    assert record is not None
    assert record.produced_mana == ()
    assert record.keywords == ()
    assert record.game_changer is False


# --- content_hash ----------------------------------------------------------


def test_content_hash_is_a_sha256_hex_digest() -> None:
    record = normalise(SOL_RING)
    assert record is not None

    assert re.fullmatch(r"[0-9a-f]{64}", content_hash(record))


def test_content_hash_ignores_prices_images_rank_and_printing() -> None:
    reprinted = card(
        SOL_RING,
        prices={"usd": "0.99"},
        image_uris={"normal": "https://cards.scryfall.io/normal/front/other.jpg"},
        edhrec_rank=2,
        set="ltc",
        set_type="commander",
        scryfall_uri="https://scryfall.com/card/ltc/1/sol-ring",
    )
    original, changed = normalise(SOL_RING), normalise(reprinted)
    assert original is not None and changed is not None

    assert content_hash(original) == content_hash(changed)


def test_content_hash_changes_when_the_oracle_text_changes() -> None:
    errata = card(SOL_RING, oracle_text="{T}: Add {C}{C}{C}.")
    original, changed = normalise(SOL_RING), normalise(errata)
    assert original is not None and changed is not None

    assert content_hash(original) != content_hash(changed)


def test_content_hash_changes_when_commander_legality_changes() -> None:
    banned = card(SOL_RING, legalities={"commander": "banned"})
    original, changed = normalise(SOL_RING), normalise(banned)
    assert original is not None and changed is not None

    assert content_hash(original) != content_hash(changed)


# --- power and toughness ---------------------------------------------------


def test_a_non_creature_has_no_power_or_toughness() -> None:
    record = normalise(SOL_RING)

    assert record is not None
    assert (record.power, record.toughness) == (None, None)


def test_power_and_toughness_come_from_the_top_level() -> None:
    record = normalise(ADVENTURE)

    assert record is not None
    assert (record.power, record.toughness) == ("4", "3")


def test_a_double_faced_card_takes_power_and_toughness_from_its_front_face() -> None:
    # Outside the game, a double-faced card has its front face's characteristics.
    record = normalise(TRANSFORM)

    assert record is not None
    assert (record.power, record.toughness) == ("1", "1")


def test_a_card_that_is_a_creature_only_on_its_back_face_has_none() -> None:
    back_only = card(TRANSFORM)
    for key in ("power", "toughness"):
        del back_only["card_faces"][0][key]

    record = normalise(back_only)

    assert record is not None
    assert (record.power, record.toughness) == (None, None)


def test_power_is_kept_as_text_because_it_is_not_always_a_number() -> None:
    record = normalise(card(SOL_RING, power="1+*", toughness="*"))

    assert record is not None
    assert (record.power, record.toughness) == ("1+*", "*")


@pytest.mark.parametrize(
    ("stats", "expected"),
    [({"power": "5", "toughness": "5"}, ("5", "5")), ({}, (None, None))],
)
def test_spacecraft_with_and_without_power_toughness_boxes(
    stats: dict[str, str], expected: tuple[str | None, str | None]
) -> None:
    # Inspirit, Flagship Vessel has 5/5; The Eternity Elevator has none.
    spacecraft = card(SOL_RING, type_line="Legendary Artifact — Spacecraft", **stats)

    record = normalise(spacecraft)

    assert record is not None
    assert (record.power, record.toughness) == expected


def test_content_hash_changes_when_power_changes() -> None:
    original, buffed = normalise(ADVENTURE), normalise(card(ADVENTURE, power="5"))
    assert original is not None and buffed is not None

    assert content_hash(original) != content_hash(buffed)
