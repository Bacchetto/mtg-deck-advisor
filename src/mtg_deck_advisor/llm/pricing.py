"""Model prices, used to cost every call and to enforce budgets (MOD-3).

Anthropic's published first-party rates, in US dollars per million tokens, as
of 2026-10-02. Cache writes are the 5-minute rate. A test pins these values, so
a price change is a deliberate, reviewed edit.
"""

from pydantic import BaseModel, ConfigDict

from mtg_deck_advisor.llm.types import Usage


class ModelPrice(BaseModel):
    model_config = ConfigDict(frozen=True)

    input: float
    output: float
    cache_read: float
    cache_write: float


PRICES: dict[str, ModelPrice] = {
    "claude-opus-5-5": ModelPrice(input=4.00, output=20.00, cache_read=0.20, cache_write=5.00),
    "claude-sonnet-5-5": ModelPrice(input=2.00, output=10.00, cache_read=0.20, cache_write=2.50),
    "claude-haiku-4-5": ModelPrice(input=1.00, output=5.00, cache_read=0.10, cache_write=1.25),
    # The dated name the API reports in responses to claude-haiku-4-5 requests.
    "claude-haiku-4-5-20251001": ModelPrice(
        input=1.00, output=5.00, cache_read=0.10, cache_write=1.25
    ),
    # Models a server-side refusal fallback can answer from, which bill at
    # their own rates. Cache rates are the standard 0.1x (read) and 1.25x
    # (write) of input.
    "claude-opus-5": ModelPrice(input=5.00, output=25.00, cache_read=0.50, cache_write=6.25),
    "claude-opus-4-8": ModelPrice(input=5.00, output=25.00, cache_read=0.50, cache_write=6.25),
    "claude-sonnet-5": ModelPrice(input=2.00, output=10.00, cache_read=0.20, cache_write=2.50),
}

PER_MILLION = 1_000_000


def cost_usd(model: str, usage: Usage) -> float:
    price = PRICES[model]
    return (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_read_input_tokens * price.cache_read
        + usage.cache_creation_input_tokens * price.cache_write
    ) / PER_MILLION


def worst_case_cost_usd(model: str, input_tokens: int, max_tokens: int) -> float:
    """The most a call could cost: all its input uncached, and all max_tokens of output used."""
    return cost_usd(model, Usage(input_tokens=input_tokens, output_tokens=max_tokens))
