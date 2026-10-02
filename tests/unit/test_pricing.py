"""Model prices, pinned: a change here should be deliberate and reviewed."""

import pytest

from mtg_deck_advisor.llm.pricing import PRICES, cost_usd
from mtg_deck_advisor.llm.types import Usage


@pytest.mark.parametrize(
    ("model", "input_price", "output_price"),
    [
        ("claude-opus-5-5", 4.00, 20.00),
        ("claude-sonnet-5-5", 2.00, 10.00),
        ("claude-haiku-4-5", 1.00, 5.00),
        # Models a server-side refusal fallback can answer from.
        ("claude-opus-5", 5.00, 25.00),
        ("claude-opus-4-8", 5.00, 25.00),
        ("claude-sonnet-5", 2.00, 10.00),
    ],
)
def test_prices_per_million_tokens(model: str, input_price: float, output_price: float) -> None:
    assert (PRICES[model].input, PRICES[model].output) == (input_price, output_price)


def test_cost_counts_every_kind_of_token() -> None:
    usage = Usage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_input_tokens=1_000_000,
        cache_creation_input_tokens=1_000_000,
    )
    price = PRICES["claude-sonnet-5-5"]

    assert cost_usd("claude-sonnet-5-5", usage) == pytest.approx(
        price.input + price.output + price.cache_read + price.cache_write
    )


def test_a_dated_model_alias_is_priced_like_its_model() -> None:
    # The API answered Haiku 4.5 requests as claude-haiku-4-5-20251001.
    assert PRICES["claude-haiku-4-5-20251001"] == PRICES["claude-haiku-4-5"]
