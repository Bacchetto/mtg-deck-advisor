"""Validating the exact filters for card search."""

import pytest
from pydantic import ValidationError

from mtg_deck_advisor.retrieval.search import CardFilters


def test_no_filters_by_default() -> None:
    filters = CardFilters()

    assert filters.color_identity_within is None and filters.oracle_ids is None
    assert filters.types == [] and filters.mana_value_min is None


@pytest.mark.parametrize("identity", ["", "W", "WUBRG", "BG"])
def test_a_color_identity_is_letters_from_wubrg(identity: str) -> None:
    assert CardFilters(color_identity_within=identity).color_identity_within == identity


@pytest.mark.parametrize("identity", ["X", "wu", "C", "UU"])
def test_anything_else_is_not_a_color_identity(identity: str) -> None:
    with pytest.raises(ValidationError):
        CardFilters(color_identity_within=identity)


def test_a_type_is_one_word() -> None:
    with pytest.raises(ValidationError):
        CardFilters(types=["Legendary Creature"])


def test_a_mana_value_range_must_not_be_backwards() -> None:
    with pytest.raises(ValidationError):
        CardFilters(mana_value_min=4, mana_value_max=2)
