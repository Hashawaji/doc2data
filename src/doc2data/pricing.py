"""List-price cost of a model call, so every document carries what it cost to read.

Prices are USD per one million tokens (input, output), taken from the provider's
pricing page on 2026-11-24. They change; override them with DOC2DATA_PRICE_INPUT_PER_M
and DOC2DATA_PRICE_OUTPUT_PER_M. On a free tier nothing is charged, so treat the figure
as what the same volume would cost on a paid key.

A model with no known price costs None, not zero, so the dashboard says "not priced"
and does not understate spend.
"""

from __future__ import annotations

import os
from decimal import Decimal

from .extractors.base import Usage

MILLION = Decimal(1_000_000)
PRICES_PER_MILLION: dict[str, tuple[Decimal, Decimal]] = {
    "gemini-3.5-flash-lite": (Decimal("0.30"), Decimal("2.50")),
}


def _price(model: str) -> tuple[Decimal, Decimal] | None:
    """Table price with either side overridden from the environment; None if a side is unknown."""
    listed_in, listed_out = PRICES_PER_MILLION.get(model, (None, None))
    price_in = os.environ.get("DOC2DATA_PRICE_INPUT_PER_M", listed_in)
    price_out = os.environ.get("DOC2DATA_PRICE_OUTPUT_PER_M", listed_out)
    if price_in is None or price_out is None:
        return None
    return Decimal(price_in), Decimal(price_out)


def cost_usd(model: str | None, usage: Usage) -> Decimal | None:
    """Zero for extractors that call no model; None when the model's price is unknown."""
    if model is None:
        return Decimal("0")
    price = _price(model)
    if price is None:
        return None
    cost = (usage.input_tokens * price[0] + usage.output_tokens * price[1]) / MILLION
    return cost.quantize(Decimal("0.000001"))
