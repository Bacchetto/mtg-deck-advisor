"""The Commander validator on hand-built card facts: one rule at a time, then properties."""

import re
from uuid import UUID

from hypothesis import given
from hypothesis import strategies as st

from mtg_deck_advisor.deck.facts import CardFacts, Eligibility
from mtg_deck_advisor.deck.state import DeckState
from mtg_deck_advisor.guardrails.commander import ValidationResult, validate
from mtg_deck_advisor.ingestion.cards import CommanderLegality

ELIGIBLE = Eligibility(eligible=True, rule="903.3", reason="a legendary creature")
NOT_ELIGIBLE = Eligibility(eligible=False, rule="903.3", reason="it is not legendary")
_ids = iter(range(1, 10_000))


def card(
    name: str,
    colors: str = "",
    *,
    legality: CommanderLegality = "legal",
    copy_limit: int | None = 1,
    basic: bool = False,
    free: bool = False,
    eligibility: Eligibility = NOT_ELIGIBLE,
) -> CardFacts:
    return CardFacts(
        oracle_id=UUID(int=next(_ids)),
        name=name,
        color_identity=frozenset(colors),
        commander_legality=legality,
        is_basic_land=basic,
        is_free_basic=free,
        copy_limit=copy_limit,
        commander_eligibility=eligibility,
    )


ATRAXA = card("Atraxa, Praetors' Voice", "WUBG", eligibility=ELIGIBLE)
SOL_RING = card("Sol Ring")
BOLT = card("Lightning Bolt", "R")
MOX_JET = card("Mox Jet", "B", legality="banned")
RED_HERRING = card("Red Herring", "R", legality="not_legal")
DELVER = card("Delver of Secrets", "U")
RATS = card("Relentless Rats", "B", copy_limit=None)
DWARVES = card("Seven Dwarves", "R", copy_limit=7)
FOREST = card("Forest", "G", copy_limit=None, basic=True, free=True)
SNOW_ISLAND = card("Snow-Covered Island", "U", copy_limit=None, basic=True)
FILLER = [card(f"Filler {n}", "G") for n in range(98)]

ALL = [ATRAXA, SOL_RING, BOLT, MOX_JET, RED_HERRING, DELVER, RATS, DWARVES, FOREST, SNOW_ISLAND]
FACTS = {c.oracle_id: c for c in [*ALL, *FILLER]}


def legal_deck() -> tuple[DeckState, dict[UUID, int]]:
    """Atraxa, Sol Ring and 98 fillers: legal, with every card in the pool."""
    cards = {SOL_RING.oracle_id: 1} | {c.oracle_id: 1 for c in FILLER}
    pool = {ATRAXA.oracle_id: 1} | cards
    return DeckState.new(ATRAXA.oracle_id, cards), pool


def swap(deck: DeckState, out: CardFacts, into: CardFacts, count: int = 1) -> DeckState:
    return deck.without_card(out.oracle_id).with_card(into.oracle_id, count)


def codes(result: ValidationResult) -> list[str]:
    return [violation.code for violation in result.violations]


# --- examples --------------------------------------------------------------


def test_a_legal_deck_has_no_violations() -> None:
    deck, pool = legal_deck()

    result = validate(deck, FACTS, pool)

    assert result.valid
    assert result.violations == []


def test_a_deck_must_have_exactly_100_cards() -> None:
    deck, pool = legal_deck()

    [violation] = validate(deck.without_card(SOL_RING.oracle_id), FACTS, pool).violations

    assert violation.code == "deck_size"
    assert violation.rule == "903.5a"
    assert "99" in violation.message


def test_deck_size_can_be_left_out_while_a_deck_is_being_built() -> None:
    deck, pool = legal_deck()

    result = validate(deck.without_card(SOL_RING.oracle_id), FACTS, pool, check_size=False)

    assert result.valid


def test_the_commander_must_be_eligible() -> None:
    deck, pool = legal_deck()
    deck = deck.with_commander(DELVER.oracle_id)
    pool[DELVER.oracle_id] = 1

    result = validate(deck, FACTS, pool)

    assert "commander_not_eligible" in codes(result)
    violation = next(v for v in result.violations if v.code == "commander_not_eligible")
    assert violation.rule == "903.3"
    assert violation.card == DELVER.oracle_id
    assert "not legendary" in violation.message


def test_a_card_outside_the_commanders_color_identity_is_reported() -> None:
    deck, pool = legal_deck()
    pool[BOLT.oracle_id] = 1

    [violation] = validate(swap(deck, FILLER[0], BOLT), FACTS, pool).violations

    assert violation.code == "color_identity"
    assert violation.rule == "903.5c"
    assert violation.card == BOLT.oracle_id
    assert "Lightning Bolt" in violation.message


def test_banned_cards_are_reported() -> None:
    deck, pool = legal_deck()
    pool[MOX_JET.oracle_id] = 1

    [violation] = validate(swap(deck, FILLER[0], MOX_JET), FACTS, pool).violations

    assert violation.code == "banned"
    assert violation.rule == "banned_list"


def test_cards_that_are_not_legal_in_commander_are_reported() -> None:
    # Joke and digital-only cards are not part of the format at all.
    esika = card("Esika", "WUBRG", eligibility=ELIGIBLE)
    deck, pool = legal_deck()
    deck = swap(deck.with_commander(esika.oracle_id), FILLER[0], RED_HERRING)
    pool |= {esika.oracle_id: 1, RED_HERRING.oracle_id: 1}

    result = validate(deck, FACTS | {esika.oracle_id: esika}, pool)

    assert codes(result) == ["not_legal"]
    assert result.violations[0].rule == "format_legality"


def test_a_second_copy_of_a_singleton_card_is_reported() -> None:
    deck, pool = legal_deck()
    pool[SOL_RING.oracle_id] = 2

    [violation] = validate(swap(deck, FILLER[0], SOL_RING), FACTS, pool).violations

    assert violation.code == "too_many_copies"
    assert violation.rule == "903.5b"
    assert "2" in violation.message


def test_the_commander_counts_as_a_copy() -> None:
    deck, pool = legal_deck()
    pool[ATRAXA.oracle_id] = 2

    result = validate(swap(deck, FILLER[0], ATRAXA), FACTS, pool)

    assert codes(result) == ["too_many_copies"]


def test_cards_that_allow_any_number_are_limited_only_by_the_pool() -> None:
    deck, pool = legal_deck()
    for filler in FILLER[:20]:
        deck = deck.without_card(filler.oracle_id)
    deck = deck.with_card(RATS.oracle_id, 20)
    pool[RATS.oracle_id] = 20

    assert validate(deck, FACTS, pool).valid

    pool[RATS.oracle_id] = 15
    [violation] = validate(deck, FACTS, pool).violations
    assert violation.code == "exceeds_owned"
    assert violation.rule == "pool"
    assert "15" in violation.message


def test_an_up_to_n_card_is_capped_at_n() -> None:
    esika = card("Esika", "WUBRG", eligibility=ELIGIBLE)
    deck = DeckState.new(esika.oracle_id, {DWARVES.oracle_id: 8, FOREST.oracle_id: 91})
    facts = FACTS | {esika.oracle_id: esika}
    pool = {esika.oracle_id: 1, DWARVES.oracle_id: 8}

    [violation] = validate(deck, facts, pool).violations

    assert violation.code == "too_many_copies"
    assert "7" in violation.message


def test_the_five_basics_need_no_pool() -> None:
    deck, pool = legal_deck()
    for filler in FILLER[:40]:
        deck = deck.without_card(filler.oracle_id)

    assert validate(deck.with_card(FOREST.oracle_id, 40), FACTS, pool).valid


def test_other_basic_lands_must_come_from_the_pool() -> None:
    esika = card("Esika", "WUBRG", eligibility=ELIGIBLE)
    deck = DeckState.new(esika.oracle_id, {SNOW_ISLAND.oracle_id: 10, FOREST.oracle_id: 89})
    facts = FACTS | {esika.oracle_id: esika}

    [violation] = validate(deck, facts, {esika.oracle_id: 1}).violations
    assert violation.code == "not_in_pool"

    assert validate(deck, facts, {esika.oracle_id: 1, SNOW_ISLAND.oracle_id: 10}).valid


def test_cards_missing_from_the_pool_are_reported() -> None:
    deck, pool = legal_deck()
    del pool[SOL_RING.oracle_id]

    [violation] = validate(deck, FACTS, pool).violations

    assert violation.code == "not_in_pool"
    assert violation.rule == "pool"
    assert violation.card == SOL_RING.oracle_id


def test_the_commander_must_be_in_the_pool() -> None:
    deck, pool = legal_deck()
    del pool[ATRAXA.oracle_id]

    assert codes(validate(deck, FACTS, pool)) == ["not_in_pool"]


def test_unknown_cards_are_reported_and_not_checked_further() -> None:
    deck, pool = legal_deck()
    stranger = UUID(int=999_999)
    pool[stranger] = 1

    [violation] = validate(
        deck.without_card(SOL_RING.oracle_id).with_card(stranger), FACTS, pool
    ).violations

    assert violation.code == "unknown_card"
    assert violation.card == stranger


def test_every_violation_is_reported_not_just_the_first() -> None:
    deck, pool = legal_deck()
    deck = swap(deck, FILLER[0], BOLT)
    deck = swap(deck, FILLER[1], MOX_JET)
    pool[MOX_JET.oracle_id] = 1

    result = validate(deck.without_card(SOL_RING.oracle_id), FACTS, pool)

    assert sorted(codes(result)) == ["banned", "color_identity", "deck_size", "not_in_pool"]


def test_violations_come_in_a_stable_order_with_deck_size_last() -> None:
    deck, pool = legal_deck()
    deck = swap(deck, FILLER[0], BOLT).without_card(SOL_RING.oracle_id)

    first, second = validate(deck, FACTS, pool), validate(deck, FACTS, pool)

    assert first.violations == second.violations
    assert first.violations[-1].code == "deck_size"


# --- properties ------------------------------------------------------------

COLORS = st.frozensets(st.sampled_from("WUBRG"))


@given(commander_colors=COLORS, card_colors=COLORS)
def test_a_card_is_reported_off_color_exactly_when_it_is(
    commander_colors: frozenset[str], card_colors: frozenset[str]
) -> None:
    commander = card("Commander", "".join(commander_colors), eligibility=ELIGIBLE)
    tested = card("Tested", "".join(card_colors))
    facts = {commander.oracle_id: commander, tested.oracle_id: tested}
    deck = DeckState.new(commander.oracle_id, {tested.oracle_id: 1})
    pool = {commander.oracle_id: 1, tested.oracle_id: 1}

    result = validate(deck, facts, pool, check_size=False)

    assert ("color_identity" in codes(result)) == (not card_colors <= commander_colors)


@given(st.integers(min_value=0, max_value=97), st.integers(min_value=0, max_value=97))
def test_swapping_a_legal_card_for_another_keeps_a_legal_deck_legal(out: int, into: int) -> None:
    deck, pool = legal_deck()
    replacement = card(f"Replacement {into}", "G")
    facts = FACTS | {replacement.oracle_id: replacement}
    pool[replacement.oracle_id] = 1

    assert validate(swap(deck, FILLER[out], replacement), facts, pool).valid


RULE = re.compile(r"^\d{3}\.\d+[a-z]*$|^(banned_list|format_legality|pool|card_data)$")


@given(st.lists(st.sampled_from(ALL), max_size=12), st.sampled_from(ALL))
def test_every_violation_names_a_rule_and_explains_itself(
    picked: list[CardFacts], commander: CardFacts
) -> None:
    deck = DeckState.new(commander.oracle_id)
    for picked_card in picked:
        deck = deck.with_card(picked_card.oracle_id)

    for violation in validate(deck, FACTS, {}).violations:
        assert RULE.match(violation.rule), violation.rule
        assert violation.message
