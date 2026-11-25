"""The pipeline: file in, checked rows out.

    bytes -> hash (already read? stop) -> text -> extract -> validate -> store

A document ends as `accepted`, `needs_review` (a check failed, or it looks like a
duplicate invoice) or `failed` (unreadable file, or the extractor gave up). Nothing
is dropped silently: failures are stored with their reason.

Re-reading. A file is skipped only if the same extractor has already read it
successfully. A failed read is tried again, a different extractor reads the file
afresh, and `force` re-reads on request. A re-read updates the same row, so the
document keeps its id. It replaces the earlier invoice and clears any approval, but
only if it succeeds: when a re-read fails, the earlier result is left untouched and
the error is returned to the caller.

ingest_bytes commits the session before the extractor runs, so no database connection
is held during a slow extraction, and again when it stores the result. Anything the
caller left pending in the session is saved by the first commit.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from .db import ACCEPTED, FAILED, NEEDS_REVIEW, Document, InvoiceRow, IssueRow, LineItemRow, utcnow
from .extractors.base import ExtractionError, ExtractionResult, Extractor
from .pdf_text import UnreadableDocument, extract_text
from .pricing import cost_usd
from .schema import Invoice, name_key
from .validation import ERROR, Issue, needs_review, validate


@dataclass(frozen=True)
class Outcome:
    document: Document
    created: bool  # False when the file was skipped, or a re-read failed and nothing changed
    error: str | None = None  # why a re-read failed; the document is the earlier result


def _earlier_copy(session: Session, invoice: Invoice, own_id: int) -> Document | None:
    """Another document that already carries this vendor and invoice number.

    A document that is itself marked as a copy of this one is left out. Otherwise
    re-reading an original would turn it into a copy of its own copy. Every other
    document counts, whatever its status: a copy still waiting for review holds its
    number against a third arrival just as an accepted document does.
    """
    vendor_key = name_key(invoice.vendor_name)
    if not (vendor_key and invoice.invoice_number):
        return None
    return session.scalar(
        select(Document)
        .join(InvoiceRow)
        .where(
            InvoiceRow.vendor_key == vendor_key,
            InvoiceRow.invoice_number == invoice.invoice_number,
            Document.id != own_id,
            Document.duplicate_of_id.is_distinct_from(own_id),
        )
        .order_by(Document.id)
        .limit(1)
    )


def _invoice_row(invoice: Invoice) -> InvoiceRow:
    return InvoiceRow(
        vendor_name=invoice.vendor_name,
        vendor_key=name_key(invoice.vendor_name),
        customer_name=invoice.customer_name,
        invoice_number=invoice.invoice_number,
        invoice_date=invoice.invoice_date,
        due_date=invoice.due_date,
        currency=invoice.currency,
        subtotal=invoice.subtotal,
        tax=invoice.tax,
        total=invoice.total,
        line_items=[
            LineItemRow(
                position=position,
                description=item.description,
                quantity=item.quantity,
                unit_price=item.unit_price,
                amount=item.amount,
            )
            for position, item in enumerate(invoice.line_items, start=1)
        ],
    )


def _find(session: Session, sha256: str) -> Document | None:
    return session.scalar(select(Document).where(Document.sha256 == sha256))


def _store_failure(
    session: Session, sha256: str, filename: str, size_bytes: int, extractor: Extractor, error: str
) -> Outcome:
    """Record a failed read without ever discarding an earlier good result."""
    existing = _find(session, sha256)
    if existing is not None and existing.status != FAILED:
        return Outcome(existing, created=False, error=error)
    document = existing or Document(sha256=sha256, filename=filename, size_bytes=size_bytes)
    document.status = FAILED
    document.extractor = extractor.name
    document.error = error
    session.add(document)
    try:
        session.commit()
    except IntegrityError:
        # The same file was stored by another request a moment ago. That record stands.
        session.rollback()
        stored = _find(session, sha256)
        if stored is None:
            raise
        return Outcome(stored, created=False, error=None if stored.status == FAILED else error)
    return Outcome(document, created=True)


def _store_result(
    session: Session,
    document: Document,
    extractor: Extractor,
    result: ExtractionResult,
    pages: int,
    today: date | None,
) -> None:
    # Clear the previous reading first; the database allows one invoice per document.
    previous = document.invoice
    if previous is not None:
        before = (previous.vendor_key, previous.invoice_number)
        if before != (name_key(result.invoice.vendor_name), result.invoice.invoice_number):
            # Copies a person approved against this document matched what it said before.
            # That approval does not cover a match this new reading may make with them.
            for copy in session.scalars(
                select(Document).where(Document.duplicate_of_id == document.id, Document.status == ACCEPTED)
            ):
                copy.duplicate_of_id = None
        session.delete(previous)
        document.invoice = None
    document.issues.clear()
    session.flush()  # also gives a new document its id, which duplicate detection excludes

    issues = validate(result.invoice, today=today)
    earlier = _earlier_copy(session, result.invoice, own_id=document.id)
    if earlier is not None:
        issues.append(
            Issue(
                ERROR,
                "possible_duplicate",
                f"Same vendor and invoice number as document {earlier.id} ({earlier.filename}).",
            )
        )
    document.duplicate_of_id = earlier.id if earlier is not None else None

    document.status = NEEDS_REVIEW if needs_review(issues) else ACCEPTED
    document.reviewed_at = None
    document.extractor = extractor.name
    document.pages = pages
    document.model = result.model
    document.input_tokens = result.usage.input_tokens
    document.output_tokens = result.usage.output_tokens
    document.cost_usd = cost_usd(result.model, result.usage)
    document.latency_ms = result.latency_ms
    document.raw_output = result.raw_output
    document.error = None
    document.invoice = _invoice_row(result.invoice)
    document.issues = [IssueRow(severity=i.severity, code=i.code, message=i.message) for i in issues]
    session.commit()


def ingest_bytes(
    session: Session,
    data: bytes,
    filename: str,
    extractor: Extractor,
    *,
    today: date | None = None,
    force: bool = False,
) -> Outcome:
    sha256 = hashlib.sha256(data).hexdigest()
    existing = _find(session, sha256)
    if existing is not None and not force:
        if existing.status != FAILED and existing.extractor == extractor.name:
            return Outcome(existing, created=False)

    # Ending the transaction returns the connection to the pool, so a slow extraction
    # does not hold one for its whole length. A commit, not a rollback: a rollback would
    # discard the caller's pending changes and expire the objects it already holds.
    session.commit()

    # Read before touching the database, so a failure cannot cost an earlier result.
    try:
        text, pages = extract_text(data)
        result = extractor.extract(text=text, pdf_bytes=data)
    except (UnreadableDocument, ExtractionError) as exc:
        return _store_failure(session, sha256, filename, len(data), extractor, str(exc))

    document = existing
    if document is None:
        document = Document(
            sha256=sha256, filename=filename, size_bytes=len(data), status=FAILED, extractor=extractor.name
        )
        session.add(document)
    try:
        _store_result(session, document, extractor, result, pages, today)
    except SQLAlchemyError as exc:
        session.rollback()  # restores any earlier row
        if existing is None and isinstance(exc, IntegrityError):
            stored = _find(session, sha256)
            if stored is not None:
                # The same file arrived twice at once and the other request stored it
                # first. Its record stands, and this is not a failure.
                return Outcome(stored, created=False)
        # The extraction held a value the database refused. The file still gets a
        # record of what happened.
        return _store_failure(
            session, sha256, filename, len(data), extractor, f"Could not store the result: {exc}"[:500]
        )
    return Outcome(document, created=True)


def ingest_file(
    session: Session,
    path: Path,
    extractor: Extractor,
    *,
    today: date | None = None,
    force: bool = False,
) -> Outcome:
    return ingest_bytes(session, path.read_bytes(), path.name, extractor, today=today, force=force)


def _same_invoice(document: Document | None, other: Document) -> bool:
    return (
        document is not None
        and document.invoice is not None
        and other.invoice is not None
        and document.invoice.vendor_key == other.invoice.vendor_key
        and document.invoice.invoice_number == other.invoice.invoice_number
    )


def approve(session: Session, document_id: int) -> Document | None:
    """A person has checked a flagged document and accepts it as it stands.

    A copy approved while the document it was flagged against still carries the same
    invoice stays linked to it, so re-reading that document does not flag it in turn.
    If that document has since been read with another number, the person approved an
    out-of-date flag; the link is dropped, so a later match with it is flagged again.
    """
    document = session.get(Document, document_id)
    if document is None or document.status != NEEDS_REVIEW:
        return document
    if document.duplicate_of_id is not None and not _same_invoice(
        session.get(Document, document.duplicate_of_id), document
    ):
        document.duplicate_of_id = None
    document.status = ACCEPTED
    document.reviewed_at = utcnow()
    session.commit()
    return document
