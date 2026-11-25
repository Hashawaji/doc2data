from decimal import Decimal

import pytest

from doc2data.schema import Invoice, LineItem, name_key


def test_currency_must_be_a_three_letter_code():
    assert Invoice(currency="usd").currency == "USD"
    assert Invoice(currency="US dollars").currency is None
    assert Invoice(currency="$").currency is None


def test_text_is_trimmed_to_what_the_database_can_hold():
    invoice = Invoice(vendor_name="  Acme   Retail  " + "x" * 400, invoice_number="N" * 200)
    assert invoice.vendor_name.startswith("Acme Retail x") and len(invoice.vendor_name) == 255
    assert len(invoice.invoice_number) == 64


def test_absurd_amounts_are_dropped_so_validation_flags_them():
    assert Invoice(total="70368744177664.01").total is None
    assert Invoice(total="999999999999.99").total == Decimal("999999999999.99")


def test_name_key_ignores_case_punctuation_and_spacing_in_any_script():
    assert name_key("Ørsted Services A/S") == name_key("ØRSTED  SERVICES A-S")
    assert name_key("Harrow & Pike Ltd.") == name_key("harrow pike ltd")
    assert name_key("  ") is None and name_key(None) is None


@pytest.mark.parametrize("value", ["NaN", float("nan"), "Infinity", "-inf", "twelve"])
def test_values_that_are_not_finite_numbers_are_rejected(value):
    # A ValueError, so the extractor turns it into a recorded failure instead of a crash.
    with pytest.raises(ValueError):
        Invoice(total=value)
    with pytest.raises(ValueError):
        LineItem(description="x", quantity=value)


def test_name_key_treats_composed_and_decomposed_accents_alike():
    assert name_key("Caf\u00e9 Lumi\u00e8re") == name_key("Cafe\u0301 Lumie\u0300re")
