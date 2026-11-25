"""What every extractor has to provide."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..schema import Invoice


class ExtractionError(RuntimeError):
    """The extractor could not produce an invoice for this document."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class ExtractionResult:
    invoice: Invoice
    extractor: str
    model: str | None = None
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0
    raw_output: str | None = None


class Extractor(Protocol):
    name: str

    def extract(self, *, text: str, pdf_bytes: bytes | None = None) -> ExtractionResult:
        """Read one invoice. `text` is the PDF's text layer; `pdf_bytes` is the file itself."""
        ...
