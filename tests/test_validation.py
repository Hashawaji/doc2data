from datetime import date
from decimal import Decimal

from doc2data.validation import needs_review, validate

TODAY = date(2026, 11, 24)


def codes(invoice) -> set[str]:
    return {issue.code for issue in validate(invoice, today=TODAY)}


def test_a_consistent_invoice_has_no_issues(good_invoice):
    assert validate(good_invoice, today=TODAY) == []


def test_missing_required_fields_are_errors(good_invoice):
    broken = good_invoice.model_copy(update={"vendor_name": None, "total": None})
    assert {"missing_vendor_name", "missing_total"} <= codes(broken)
    assert needs_review(validate(broken, today=TODAY))


def test_total_that_does_not_add_up_is_an_error(good_invoice):
    assert "totals_mismatch" in codes(good_invoice.model_copy(update={"total": Decimal("208.25")}))


def test_rounding_differences_within_two_cents_pass(good_invoice):
    assert "totals_mismatch" not in codes(good_invoice.model_copy(update={"total": Decimal("108.26")}))


def test_line_items_must_add_up_to_the_amount_before_tax(good_invoice):
    item = good_invoice.line_items[0].model_copy(update={"amount": Decimal("90.00")})
    assert "line_items_mismatch" in codes(good_invoice.model_copy(update={"line_items": [item]}))


def test_line_items_are_checked_against_total_minus_tax_when_no_subtotal(good_invoice):
    assert "line_items_mismatch" not in codes(good_invoice.model_copy(update={"subtotal": None}))
    off = good_invoice.model_copy(update={"subtotal": None, "total": Decimal("150.00")})
    assert "line_items_mismatch" in codes(off)


def test_due_date_before_invoice_date_is_an_error(good_invoice):
    assert "due_before_issue" in codes(good_invoice.model_copy(update={"due_date": date(2026, 9, 1)}))


def test_invoice_dated_in_the_future_is_an_error(good_invoice):
    future = good_invoice.model_copy(update={"invoice_date": date(2027, 3, 1), "due_date": None})
    assert "date_in_future" in codes(future)


def test_unknown_currency_is_only_a_warning(good_invoice):
    issues = validate(good_invoice.model_copy(update={"currency": "XXX"}), today=TODAY)
    assert [issue.code for issue in issues] == ["unknown_currency"]
    assert not needs_review(issues)
