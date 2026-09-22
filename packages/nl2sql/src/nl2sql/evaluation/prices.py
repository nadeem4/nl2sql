"""USD per million tokens for the models tier 2 may run, dated and sourced.

Tier 2 prices every LLM call from this table and refuses to start if a model
it would call is missing, so a run can never spend money it cannot count.
Update a row (and its ``source``) when a provider changes its prices.

The four rates follow the engine's usage fields
(``nl2sql.services.callbacks.token_handler``): ``cached_input`` and
``cache_write`` are parts of ``input_tokens``; reasoning tokens are part of
``output_tokens`` and billed as output. OpenAI charges nothing extra to write
its automatic prompt cache, so its ``cache_write`` is the input rate (it
reports no cache writes anyway). Anthropic bills a 5-minute cache write at
1.25x input and a cache read at 0.1x input.
"""
from __future__ import annotations

from typing import Dict, NamedTuple

PRICES_CHECKED_ON = "2026-09-21"


class ModelPrice(NamedTuple):
    """USD per 1M tokens, and where the numbers came from."""

    input: float
    cached_input: float
    cache_write: float
    output: float
    verified: bool
    source: str


_OPENAI = "OpenAI pricing page, checked 2026-09-21"
_ANTHROPIC = ("Anthropic model table in the claude-api skill (cached 2026-06-24); "
              "cache write 1.25x and cache read 0.1x of input")

PRICES: Dict[str, ModelPrice] = {
    "gpt-5.4": ModelPrice(2.50, 0.25, 2.50, 15.00, True, _OPENAI),
    "gpt-5.4-mini": ModelPrice(0.75, 0.075, 0.75, 4.50, True, _OPENAI),
    "claude-opus-5": ModelPrice(5.00, 0.50, 6.25, 25.00, True, _ANTHROPIC),
    "claude-sonnet-5": ModelPrice(2.00, 0.20, 2.50, 10.00, True, _ANTHROPIC),
    "claude-haiku-4-5": ModelPrice(1.00, 0.10, 1.25, 5.00, True, _ANTHROPIC),
}


def call_cost(price: ModelPrice, *, input_tokens: int, cached_input_tokens: int,
              cache_write_input_tokens: int, output_tokens: int) -> float:
    """Dollars for one call. Cached and cache-write tokens are subsets of ``input_tokens``."""
    uncached = max(input_tokens - cached_input_tokens - cache_write_input_tokens, 0)
    return (uncached * price.input
            + cached_input_tokens * price.cached_input
            + cache_write_input_tokens * price.cache_write
            + output_tokens * price.output) / 1_000_000
