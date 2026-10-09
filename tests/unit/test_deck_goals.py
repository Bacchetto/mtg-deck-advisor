"""Goals a change states, as the agent sends them (#136)."""

import pytest
from pydantic import ValidationError

from mtg_deck_advisor.deck.goals import Goal


@pytest.mark.parametrize("kind", ["role_change", "role_added"])
def test_a_role_goal_with_no_count_means_at_least_one(kind: str) -> None:
    # Live, the agent sent {"kind": "role_change", "role": "removal"} for "add
    # more removal" 182 times in one eval, and every call was rejected.
    goal = Goal.model_validate({"kind": kind, "role": "removal", "max_mana_value": None})

    assert goal.at_least == 1
    assert goal.describe().startswith(("removal up by 1", "1 or more removal"))


@pytest.mark.parametrize("kind", ["type_change", "type_cut"])
def test_a_type_goal_with_no_count_means_at_least_one(kind: str) -> None:
    assert Goal.model_validate({"kind": kind, "type": "Creature"}).at_least == 1


def test_a_goal_still_needs_what_it_counts() -> None:
    with pytest.raises(ValidationError, match="role_added goal needs role"):
        Goal.model_validate({"kind": "role_added", "equals": 2})


def test_every_field_says_which_kinds_use_it() -> None:
    fields = Goal.model_fields
    for name, kinds in {
        "equals": ["lands", "lands_change", "role_added", "type_cut"],
        "at_least": ["role_change", "role_added", "type_change", "type_cut"],
        "role": ["role_change", "role_added"],
        "max_mana_value": ["role_change", "role_added"],
    }.items():
        description = fields[name].description or ""
        assert all(kind in description for kind in kinds), name
