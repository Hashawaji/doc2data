"""Measure an extractor against the labelled samples.

Accuracy alone is not the number that matters. The costly failure is a wrong value
that nobody notices, so the report also counts how many wrong documents the
validation checks caught and how many slipped through.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from .extractors.base import ExtractionError, Extractor
from .pdf_text import UnreadableDocument, extract_text
from .pricing import cost_usd
from .samples import GROUND_TRUTH_FILE
from .schema import Invoice, name_key
from .validation import needs_review, validate

FIELDS = (
    "vendor_name", "customer_name", "invoice_number", "invoice_date", "due_date",
    "currency", "subtotal", "tax", "total", "line_items",
)
# A mistake in one of these puts the wrong payee, date or amount into the books.
CRITICAL = ("vendor_name", "invoice_number", "invoice_date", "currency", "total")


def field_correct(name: str, predicted: Invoice, truth: Invoice) -> bool:
    if name in ("vendor_name", "customer_name"):
        return name_key(getattr(predicted, name)) == name_key(getattr(truth, name))
    if name == "line_items":
        def amounts(invoice: Invoice) -> list[tuple]:
            return [(i.quantity, i.unit_price, i.amount) for i in invoice.line_items]
        return amounts(predicted) == amounts(truth)
    return getattr(predicted, name) == getattr(truth, name)


@dataclass(frozen=True)
class DocumentScore:
    file: str
    layout: str
    correct: dict[str, bool]
    flagged: bool
    failed: bool = False
    error: str = ""  # why the extractor gave up, when it did

    @property
    def fully_correct(self) -> bool:
        return all(self.correct.values())

    @property
    def critical_error(self) -> bool:
        return not all(self.correct[name] for name in CRITICAL)


@dataclass
class Report:
    extractor: str
    model: str | None
    documents: list[DocumentScore] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: Decimal = Decimal("0")
    priced: bool = True  # False when a model with no known price was used
    seconds: float = 0.0

    def accuracy(self, name: str | None = None) -> float:
        names = (name,) if name else FIELDS
        results = [doc.correct[n] for doc in self.documents for n in names]
        return sum(results) / len(results) if results else 0.0

    @property
    def wrong_critical(self) -> list[DocumentScore]:
        """Read, but wrong on a critical field. Documents that could not be read are counted apart."""
        return [doc for doc in self.documents if doc.critical_error and not doc.failed]

    @property
    def failures(self) -> list[DocumentScore]:
        """Documents that could not be read at all. Their fields count as wrong in the accuracy."""
        return [doc for doc in self.documents if doc.failed]

    @property
    def silent_errors(self) -> list[DocumentScore]:
        """Wrong on a critical field and not sent to review: the failures that cost money."""
        return [doc for doc in self.wrong_critical if not doc.flagged]

    def to_dict(self) -> dict:
        return {
            "extractor": self.extractor,
            "model": self.model,
            "documents": len(self.documents),
            "field_accuracy": round(self.accuracy(), 4),
            "per_field": {name: round(self.accuracy(name), 4) for name in FIELDS},
            "fully_correct": sum(doc.fully_correct for doc in self.documents),
            "wrong_on_a_critical_field": len(self.wrong_critical),
            "caught_by_validation": len(self.wrong_critical) - len(self.silent_errors),
            "silent_errors": [doc.file for doc in self.silent_errors],
            "failed_to_read": [doc.file for doc in self.failures],
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "list_price_cost_usd": str(self.cost_usd) if self.priced else None,
            "seconds": round(self.seconds, 2),
        }


def run(samples_dir: Path, extractor: Extractor) -> Report:
    truth = json.loads((samples_dir / GROUND_TRUTH_FILE).read_text())
    report = Report(extractor=extractor.name, model=getattr(extractor, "model", None))
    started = time.perf_counter()
    for file, entry in sorted(truth.items()):
        expected = Invoice.model_validate(entry["invoice"])
        data = (samples_dir / file).read_bytes()
        try:
            text, _ = extract_text(data)
            result = extractor.extract(text=text, pdf_bytes=data)
        except (UnreadableDocument, ExtractionError) as exc:
            report.documents.append(
                DocumentScore(
                    file, entry["layout"], dict.fromkeys(FIELDS, False), flagged=True, failed=True, error=str(exc)
                )
            )
            continue
        report.input_tokens += result.usage.input_tokens
        report.output_tokens += result.usage.output_tokens
        cost = cost_usd(result.model, result.usage)
        if cost is None:
            report.priced = False
        else:
            report.cost_usd += cost
        report.documents.append(
            DocumentScore(
                file=file,
                layout=entry["layout"],
                correct={name: field_correct(name, result.invoice, expected) for name in FIELDS},
                flagged=needs_review(validate(result.invoice)),
            )
        )
    report.seconds = time.perf_counter() - started
    return report


def to_markdown(report: Report) -> str:
    total = len(report.documents)
    wrong, silent = report.wrong_critical, report.silent_errors
    model = f" ({report.model})" if report.model else ""
    lines = [
        f"# Evaluation: `{report.extractor}`{model}",
        "",
        f"{total} labelled invoices across {len({d.layout for d in report.documents})} layouts.",
        "",
        "| Measure | Result |",
        "|---|---|",
        f"| Fields correct | {report.accuracy():.1%} |",
        f"| Documents with every field correct | {sum(d.fully_correct for d in report.documents)} of {total} |",
        f"| Documents wrong on a critical field | {len(wrong)} of {total} |",
        f"| ...of which validation sent to review | {len(wrong) - len(silent)} |",
        f"| ...of which slipped through unnoticed | {len(silent)} |",
        f"| Documents the extractor could not read | {len(report.failures)} of {total} |",
        f"| Tokens (input / output) | {report.input_tokens:,} / {report.output_tokens:,} |",
        f"| Cost at list price | {'$' + str(report.cost_usd) if report.priced else 'not priced'} |",
        f"| Time | {report.seconds:.1f} s |",
        "",
        "Critical fields: " + ", ".join(name.replace("_", " ") for name in CRITICAL) + ".",
        "",
        "| Field | Correct |",
        "|---|---|",
        *[f"| {name.replace('_', ' ')} | {report.accuracy(name):.0%} |" for name in FIELDS],
        "",
        "| Layout | Every field correct |",
        "|---|---|",
    ]
    for layout in sorted({d.layout for d in report.documents}):
        docs = [d for d in report.documents if d.layout == layout]
        lines.append(f"| {layout} | {sum(d.fully_correct for d in docs)} of {len(docs)} |")
    if silent:
        lines += ["", "Slipped through: " + ", ".join(f"`{d.file}`" for d in silent) + "."]
    if report.failures:
        lines += ["", "Could not be read:", ""]
        lines += [f"- `{d.file}`: {' '.join(d.error.split())[:300]}" for d in report.failures]
    return "\n".join(lines) + "\n"
