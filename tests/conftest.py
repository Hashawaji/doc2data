import json
from pathlib import Path

import pytest

from doc2data import config, samples
from doc2data.db import init_db, make_engine
from doc2data.extractors.base import ExtractionResult
from doc2data.schema import Invoice


@pytest.fixture(autouse=True)
def ignore_local_env_file(monkeypatch):
    """Keep a developer's own .env, which may hold a real API key, out of every test."""
    monkeypatch.setattr(config, "ENV_FILE", str(Path("no-such-directory") / "absent.env"))


@pytest.fixture(scope="session")
def samples_dir(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("samples")
    samples.generate(out)
    return out


@pytest.fixture(scope="session")
def truth(samples_dir) -> dict:
    return json.loads((samples_dir / samples.GROUND_TRUTH_FILE).read_text())


@pytest.fixture
def session(tmp_path):
    factory = init_db(make_engine(f"sqlite:///{tmp_path / 'test.db'}"))
    with factory() as db_session:
        yield db_session


class FakeExtractor:
    """Returns whatever invoice it is given, so tests control the extraction result."""

    name = "fake"

    def __init__(self, invoice: Invoice | None = None, error: Exception | None = None):
        self.invoice = invoice
        self.error = error
        self.calls = 0

    def extract(self, *, text: str, pdf_bytes: bytes | None = None) -> ExtractionResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return ExtractionResult(invoice=self.invoice, extractor=self.name)


class TruthExtractor:
    """A perfect extractor: looks the answer up in the ground truth by document content."""

    name = "truth"

    def __init__(self, samples_dir: Path, truth: dict):
        self.by_bytes = {
            (samples_dir / file).read_bytes(): Invoice.model_validate(entry["invoice"])
            for file, entry in truth.items()
        }

    def extract(self, *, text: str, pdf_bytes: bytes | None = None) -> ExtractionResult:
        return ExtractionResult(invoice=self.by_bytes[pdf_bytes], extractor=self.name)


@pytest.fixture
def good_invoice() -> Invoice:
    return Invoice.model_validate(
        {
            "vendor_name": "Northwind Data Co.",
            "invoice_number": "INV-1",
            "invoice_date": "2026-10-01",
            "due_date": "2026-10-15",
            "currency": "USD",
            "subtotal": "100.00",
            "tax": "8.25",
            "total": "108.25",
            "line_items": [{"description": "Work", "quantity": 2, "unit_price": 50, "amount": 100}],
        }
    )
