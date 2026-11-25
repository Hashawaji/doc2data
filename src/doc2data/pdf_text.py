"""Read the text layer of a PDF."""

from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader


class UnreadableDocument(ValueError):
    """The bytes are not a PDF we can open."""


def extract_text(pdf_bytes: bytes) -> tuple[str, int]:
    """Return (text, page count). Text is empty for scanned PDFs with no text layer."""
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # pypdf raises several unrelated error types for bad input
        raise UnreadableDocument(f"could not read PDF: {exc}") from exc
    return "\n".join(pages).strip(), len(pages)
