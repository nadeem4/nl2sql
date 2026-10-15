"""USD per million tokens, dated and sourced: what every model call is priced with (D11).

``PRICES`` is the engine's table, updated by hand when a provider changes its
prices (with the row's ``source``, and ``PRICES_CHECKED_ON``). ``llm.prices``
in the config adds a model or replaces a row (a negotiated rate, a
fine-tune); :func:`price_for` reads the config first, then the table, by the
exact name, and a model neither lists has no price, so its cost is null,
never zero.

The rates follow the engine's usage fields
(``nl2sql.services.callbacks.token_handler``): ``cached_input`` (cache reads)
and ``cache_write`` are parts of ``input_tokens``; reasoning tokens are part
of ``output_tokens`` and billed as output. OpenAI charges nothing extra to
write its automatic prompt cache, so its ``cache_write`` is the input rate (it
reports no cache writes anyway). Anthropic bills a 5-minute cache write at
1.25x input and a cache read at 0.1x input; a 1-hour cache write costs 2x
input, but the engine only ever asks for the 5-minute cache, so
``cache_write`` is the 5-minute rate.

The OpenAI rows still need verifying against OpenAI's published prices
(owner to confirm); the Anthropic rows match Anthropic's first-party prices. A config row may leave
``cached_input`` and ``cache_write`` out: a cache read then costs the input
rate, and a cache write the input rate times the wire's
``cache_write_multiplier`` (1.25 on Anthropic's wire, 1 on OpenAI's).
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, NamedTuple, Optional

PRICES_CHECKED_ON = "2026-09-21"


class ModelPrice(NamedTuple):
    """USD per 1M tokens, and where the numbers came from (``source``: the table's citation, or ``config``)."""

    input: float
    cached_input: Optional[float]
    cache_write: Optional[float]
    output: float
    verified: bool
    source: str


# Owner to confirm: not yet re-verified against OpenAI's published prices (review of PR #326).
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

CONFIG = "config"


def _field(row: Any, name: str) -> Any:
    return row.get(name) if isinstance(row, Mapping) else getattr(row, name, None)


def _from_config(row: Any) -> ModelPrice:
    """An ``llm.prices`` row (a ``Price`` or the same keys as a mapping) as a ``ModelPrice``."""
    return ModelPrice(float(_field(row, "input")),
                      None if _field(row, "cached_input") is None else float(_field(row, "cached_input")),
                      None if _field(row, "cache_write") is None else float(_field(row, "cache_write")),
                      float(_field(row, "output")), False, CONFIG)


def price_for(model: Optional[str], overrides: Optional[Mapping[str, Any]] = None) -> Optional[ModelPrice]:
    """The config's ``llm.prices`` row, else the table's, by exact name; None when neither has it.

    No prefix matching: ``gpt-5.4`` must not price ``gpt-5.4-mini``.
    """
    if not model:
        return None
    if overrides and model in overrides:
        return _from_config(overrides[model])
    return PRICES.get(model)


def price_for_call(served: Optional[str], configured: Optional[str],
                   overrides: Optional[Mapping[str, Any]] = None) -> Optional[ModelPrice]:
    """A call's price: ``llm.prices`` by the served name, then by the configured name, then the table for either.

    Every override comes before the table, so a negotiated rate for a
    deployment alias (``acme-gpt``) holds even when the provider reports a
    model the table lists (``gpt-5.4``).
    """
    for name in (served, configured):
        if name and overrides and name in overrides:
            return _from_config(overrides[name])
    return price_for(served) or price_for(configured)


def call_cost(price: ModelPrice, *, input_tokens: Optional[int], output_tokens: Optional[int],
              cached_input_tokens: Optional[int] = None, cache_write_input_tokens: Optional[int] = None,
              cache_write_multiplier: float = 1.0) -> Optional[float]:
    """Dollars for one call, or None when its input or output count is unknown.

    Cache reads and writes are subsets of ``input_tokens``; one the provider
    did not report is not split out, so that part of the prompt is billed at
    the input rate. Reasoning tokens are in ``output_tokens``.
    """
    if input_tokens is None or output_tokens is None:
        return None
    cached = cached_input_tokens or 0
    written = cache_write_input_tokens or 0
    cached_rate = price.input if price.cached_input is None else price.cached_input
    write_rate = price.input * cache_write_multiplier if price.cache_write is None else price.cache_write
    uncached = max(input_tokens - cached - written, 0)
    return round((uncached * price.input + cached * cached_rate + written * write_rate
                  + output_tokens * price.output) / 1_000_000, 8)


def effective_prices(overrides: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """The table with ``overrides`` applied, for ``engine.llm.prices()``.

    ``checked_on`` is the table's date; each row has the four rates,
    ``verified``, ``source`` (``table`` or ``config``) and ``citation`` (the
    table row's source, None for a config row).
    """
    rows = {name: price for name, price in PRICES.items()}
    rows.update({name: _from_config(row) for name, row in (overrides or {}).items()})
    return {"checked_on": PRICES_CHECKED_ON,
            "models": {name: {"input": p.input, "cached_input": p.cached_input, "cache_write": p.cache_write,
                              "output": p.output, "verified": p.verified,
                              "source": CONFIG if p.source == CONFIG else "table",
                              "citation": None if p.source == CONFIG else p.source}
                       for name, p in rows.items()}}
