"""Business checks that decide whether an extracted invoice can be trusted.

An extractor can be confidently wrong, so nothing is accepted on its say-so. Every
invoice has to pass these checks; one "error" sends the document to the review queue.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .schema import Invoice

ERROR = "error"
WARNING = "warning"

TOLERANCE = Decimal("0.02")
KNOWN_CURRENCIES = frozenset(
    "AED AUD CAD CHF CNY EUR GBP INR JPY PKR SAR SEK SGD USD".split()
)
REQUIRED_FIELDS = ("vendor_name", "invoice_number", "invoice_date", "total")


@dataclass(frozen=True)
class Issue:
    severity: str
    code: str
    message: str


def _differs(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) > TOLERANCE


def validate(invoice: Invoice, *, today: date | None = None) -> list[Issue]:
    today = today or date.today()
    issues: list[Issue] = []

    for name in REQUIRED_FIELDS:
        if getattr(invoice, name) is None:
            label = name.replace("_", " ")
            issues.append(Issue(ERROR, f"missing_{name}", f"No {label} was found."))

    if invoice.currency is None:
        issues.append(Issue(WARNING, "missing_currency", "No currency was found."))
    elif invoice.currency not in KNOWN_CURRENCIES:
        issues.append(
            Issue(WARNING, "unknown_currency", f"Unrecognised currency code {invoice.currency}.")
        )

    if invoice.total is not None and invoice.total <= 0:
        issues.append(Issue(ERROR, "non_positive_total", f"Total is {invoice.total}."))

    if invoice.subtotal is not None and invoice.total is not None:
        expected = invoice.subtotal + (invoice.tax or Decimal("0"))
        if _differs(expected, invoice.total):
            issues.append(
                Issue(
                    ERROR,
                    "totals_mismatch",
                    f"Subtotal plus tax is {expected}, but the total is {invoice.total}.",
                )
            )

    amounts = [item.amount for item in invoice.line_items if item.amount is not None]
    if amounts and len(amounts) == len(invoice.line_items):
        line_sum = sum(amounts, Decimal("0"))
        target = invoice.subtotal
        if target is None and invoice.total is not None:
            target = invoice.total - (invoice.tax or Decimal("0"))
        if target is not None and _differs(line_sum, target):
            issues.append(
                Issue(
                    ERROR,
                    "line_items_mismatch",
                    f"Line items add up to {line_sum}, but the amount before tax is {target}.",
                )
            )

    for position, item in enumerate(invoice.line_items, start=1):
        if item.quantity is None or item.unit_price is None or item.amount is None:
            continue
        if _differs(item.quantity * item.unit_price, item.amount):
            issues.append(
                Issue(
                    WARNING,
                    "line_amount_mismatch",
                    f"Line {position}: {item.quantity} x {item.unit_price} is not {item.amount}.",
                )
            )

    if invoice.invoice_date is not None:
        if invoice.invoice_date > today + timedelta(days=1):
            issues.append(
                Issue(ERROR, "date_in_future", f"Invoice date {invoice.invoice_date} is in the future.")
            )
        elif invoice.invoice_date < today - timedelta(days=5 * 365):
            issues.append(
                Issue(WARNING, "date_very_old", f"Invoice date {invoice.invoice_date} is over five years ago.")
            )
        if invoice.due_date is not None and invoice.due_date < invoice.invoice_date:
            issues.append(
                Issue(
                    ERROR,
                    "due_before_issue",
                    f"Due date {invoice.due_date} is before the invoice date {invoice.invoice_date}.",
                )
            )

    return issues


def needs_review(issues: list[Issue]) -> bool:
    return any(issue.severity == ERROR for issue in issues)
