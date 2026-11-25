from decimal import Decimal

from doc2data.extractors.base import Usage
from doc2data.pricing import cost_usd

USAGE = Usage(input_tokens=1_000_000, output_tokens=100_000)


def test_known_model_is_priced_from_the_table():
    assert cost_usd("gemini-3.5-flash-lite", USAGE) == Decimal("0.550000")


def test_no_model_costs_nothing():
    assert cost_usd(None, Usage()) == Decimal("0")


def test_unknown_model_is_unpriced_not_free():
    assert cost_usd("some-future-model", USAGE) is None


def test_prices_can_be_overridden(monkeypatch):
    monkeypatch.setenv("DOC2DATA_PRICE_INPUT_PER_M", "1")
    monkeypatch.setenv("DOC2DATA_PRICE_OUTPUT_PER_M", "10")
    assert cost_usd("some-future-model", USAGE) == Decimal("2.000000")


def test_one_override_applies_on_its_own(monkeypatch):
    monkeypatch.setenv("DOC2DATA_PRICE_OUTPUT_PER_M", "10")
    assert cost_usd("gemini-3.5-flash-lite", USAGE) == Decimal("1.300000")  # 0.30 in, 10 out
    assert cost_usd("some-future-model", USAGE) is None  # its input price is still unknown
