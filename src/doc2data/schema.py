"""The invoice shape every extractor returns, and the JSON Schema handed to a model."""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, Field, field_validator

CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("1e12")  # anything larger is a misread, and would not fit the database column
CURRENCY_CODE = re.compile(r"[A-Z]{3}")


def to_decimal(value: Any) -> Decimal | None:
    """Turn a JSON number or numeric string into a Decimal without float noise."""
    if value is None or value == "":
        return None
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value).replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"not a number: {value!r}") from exc
    if not number.is_finite():  # NaN or Infinity, which a model can emit
        raise ValueError(f"not a finite number: {value!r}")
    return number


def to_money(value: Any) -> Decimal | None:
    number = to_decimal(value)
    if number is None or abs(number) >= MAX_AMOUNT:
        return None
    return number.quantize(CENT)


def to_quantity(value: Any) -> Decimal | None:
    number = to_decimal(value)
    return None if number is None or abs(number) >= MAX_AMOUNT else number


def to_date(value: Any) -> date | None:
    """Accept a date or an ISO string. Anything else becomes None so validation flags it."""
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def clean_text(value: Any, max_length: int = 255) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())[:max_length].strip()
    return text or None


def name_key(value: str | None) -> str | None:
    """Comparison key for a name: case-folded (in any script), punctuation and spacing ignored."""
    if not value:
        return None
    folded = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\W_]+", " ", folded).strip() or None


class LineItem(BaseModel):
    description: str
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None

    @field_validator("description", mode="before")
    @classmethod
    def _description(cls, value: Any) -> str:
        return clean_text(value) or ""

    @field_validator("quantity", mode="before")
    @classmethod
    def _quantity(cls, value: Any) -> Decimal | None:
        return to_quantity(value)

    @field_validator("unit_price", "amount", mode="before")
    @classmethod
    def _money(cls, value: Any) -> Decimal | None:
        return to_money(value)


class Invoice(BaseModel):
    """One invoice. A field is None when the document does not state it."""

    vendor_name: str | None = None
    customer_name: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    line_items: list[LineItem] = Field(default_factory=list)

    @field_validator("vendor_name", "customer_name", mode="before")
    @classmethod
    def _text(cls, value: Any) -> str | None:
        return clean_text(value)

    @field_validator("invoice_number", mode="before")
    @classmethod
    def _number(cls, value: Any) -> str | None:
        return clean_text(value, max_length=64)

    @field_validator("invoice_date", "due_date", mode="before")
    @classmethod
    def _date(cls, value: Any) -> date | None:
        return to_date(value)

    @field_validator("currency", mode="before")
    @classmethod
    def _currency(cls, value: Any) -> str | None:
        """Only a three-letter code counts; 'US dollars' becomes None and is flagged as missing."""
        text = (clean_text(value) or "").upper()
        return text if CURRENCY_CODE.fullmatch(text) else None

    @field_validator("subtotal", "tax", "total", mode="before")
    @classmethod
    def _money(cls, value: Any) -> Decimal | None:
        return to_money(value)


# Kept deliberately plain (no unions, no nulls) so it works across model providers.
# A field the document does not state is simply left out.
INVOICE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "vendor_name": {
            "type": "string",
            "description": "Name of the business or person that issued the invoice.",
        },
        "customer_name": {"type": "string", "description": "Name of the party being billed."},
        "invoice_number": {
            "type": "string",
            "description": "The invoice identifier exactly as printed.",
        },
        "invoice_date": {"type": "string", "description": "Issue date as YYYY-MM-DD."},
        "due_date": {
            "type": "string",
            "description": "Payment due date as YYYY-MM-DD, only if the document states one.",
        },
        "currency": {
            "type": "string",
            "description": "ISO 4217 currency code, for example USD, EUR, GBP, PKR or AED.",
        },
        "subtotal": {"type": "number", "description": "Amount before tax, if printed."},
        "tax": {
            "type": "number",
            "description": "Total tax amount. Use 0 if the document says no tax applies.",
        },
        "total": {"type": "number", "description": "Final amount payable, as printed."},
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "quantity": {"type": "number"},
                    "unit_price": {"type": "number"},
                    "amount": {"type": "number"},
                },
                "required": ["description"],
            },
        },
    },
    "required": ["line_items"],
}
