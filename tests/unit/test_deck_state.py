"""The deck model: example tests, plus properties that must hold for every deck."""

import json
import uuid
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mtg_deck_advisor.deck.state import DeckState

COMMANDER = UUID("00000000-0000-4000-8000-000000000001")
SOL_RING = UUID("6ad8011d-3471-4369-9d68-b264cc027487")
FOREST = UUID("00000000-0000-4000-8000-0000000000f0")

# A small pool of card IDs, so generated decks often add the same card twice.
card_ids = st.sampled_from([uuid.UUID(int=n) for n in range(2, 12)])
card_counts = st.dictionaries(card_ids, st.integers(min_value=1, max_value=40), max_size=10)
decks = st.builds(DeckState.new, commander=st.just(COMMANDER), cards=card_counts)


# --- examples --------------------------------------------------------------


def test_a_new_deck_holds_just_its_commander() -> None:
    deck = DeckState.new(COMMANDER)

    assert deck.commander == COMMANDER
    assert dict(deck.cards) == {}
    assert deck.total == 1


def test_adding_and_removing_cards_returns_new_decks() -> None:
    deck = DeckState.new(COMMANDER)

    bigger = deck.with_card(SOL_RING).with_card(FOREST, 30)
    smaller = bigger.without_card(FOREST, 5)

    assert dict(deck.cards) == {}  # the original is untouched
    assert dict(bigger.cards) == {SOL_RING: 1, FOREST: 30}
    assert smaller.count(FOREST) == 25
    assert smaller.total == 27


def test_removing_the_last_copy_drops_the_card() -> None:
    deck = DeckState.new(COMMANDER, {SOL_RING: 1}).without_card(SOL_RING)

    assert SOL_RING not in deck.cards
    assert deck.count(SOL_RING) == 0


def test_removing_more_copies_than_the_deck_holds_is_an_error() -> None:
    deck = DeckState.new(COMMANDER, {SOL_RING: 1})

    with pytest.raises(ValueError, match="holds 1"):
        deck.without_card(SOL_RING, 2)


@pytest.mark.parametrize("count", [0, -1])
def test_counts_must_be_positive(count: int) -> None:
    with pytest.raises(ValueError):
        DeckState.new(COMMANDER).with_card(SOL_RING, count)
    with pytest.raises(ValueError):
        DeckState.new(COMMANDER, {SOL_RING: count})


def test_the_card_mapping_cannot_be_changed_in_place() -> None:
    deck = DeckState.new(COMMANDER, {SOL_RING: 1})

    with pytest.raises(TypeError):
        deck.cards[FOREST] = 1  # type: ignore[index]


def test_the_commander_can_be_changed() -> None:
    other = UUID("00000000-0000-4000-8000-000000000002")

    deck = DeckState.new(COMMANDER, {SOL_RING: 1}).with_commander(other)

    assert deck.commander == other
    assert dict(deck.cards) == {SOL_RING: 1}


def test_a_deck_round_trips_through_json() -> None:
    deck = DeckState.new(COMMANDER, {SOL_RING: 1, FOREST: 30})

    data = json.loads(json.dumps(deck.to_dict()))

    assert data == {
        "commander": str(COMMANDER),
        "cards": {str(SOL_RING): 1, str(FOREST): 30},
    }
    assert DeckState.from_dict(data) == deck


# --- properties ------------------------------------------------------------


@given(decks, card_ids, st.integers(min_value=1, max_value=10))
def test_adding_then_removing_a_card_gives_back_the_same_deck(
    deck: DeckState, card: UUID, count: int
) -> None:
    assert deck.with_card(card, count).without_card(card, count) == deck


@given(decks)
def test_the_total_is_the_commander_plus_every_copy(deck: DeckState) -> None:
    assert deck.total == 1 + sum(deck.cards.values())


@given(card_counts)
def test_the_order_cards_are_added_in_does_not_matter(counts: dict[UUID, int]) -> None:
    forwards, backwards = DeckState.new(COMMANDER), DeckState.new(COMMANDER)
    for card, count in counts.items():
        forwards = forwards.with_card(card, count)
    for card, count in reversed(list(counts.items())):
        backwards = backwards.with_card(card, count)

    assert forwards == backwards
    assert hash(forwards) == hash(backwards)


@given(decks)
def test_every_deck_round_trips_through_json(deck: DeckState) -> None:
    assert DeckState.from_dict(json.loads(json.dumps(deck.to_dict()))) == deck


@given(decks, card_ids)
def test_a_failed_removal_leaves_the_deck_as_it_was(deck: DeckState, card: UUID) -> None:
    before = deck.to_dict()

    with pytest.raises(ValueError):
        deck.without_card(card, deck.count(card) + 1)

    assert deck.to_dict() == before
