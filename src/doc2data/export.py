"""CSV export of everything that has been read."""

from __future__ import annotations

import csv
from typing import TextIO

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .db import Document

FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _safe(value: object) -> object:
    """Text that a spreadsheet would run as a formula is prefixed so it stays text."""
    if isinstance(value, str) and value.startswith(FORMULA_START):
        return "'" + value
    return value


COLUMNS = [
    "document_id", "filename", "status", "vendor_name", "customer_name", "invoice_number",
    "invoice_date", "due_date", "currency", "subtotal", "tax", "total", "issues",
]


def write_csv(session: Session, out: TextIO) -> int:
    """Write one row per document that produced an invoice. Returns the row count."""
    writer = csv.writer(out)
    writer.writerow(COLUMNS)
    documents = session.scalars(
        select(Document)
        .options(selectinload(Document.invoice), selectinload(Document.issues))
        .order_by(Document.id)
    ).all()
    count = 0
    for document in documents:
        invoice = document.invoice
        if invoice is None:
            continue
        row = [
            document.id, document.filename, document.status, invoice.vendor_name,
            invoice.customer_name, invoice.invoice_number, invoice.invoice_date,
            invoice.due_date, invoice.currency, invoice.subtotal, invoice.tax, invoice.total,
            "; ".join(issue.code for issue in document.issues),
        ]
        writer.writerow([_safe(value) for value in row])
        count += 1
    return count
