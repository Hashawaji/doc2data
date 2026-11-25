"""The bundled scans: image-only copies of five samples, for behaviour without a text layer."""

import json
from pathlib import Path

from doc2data import evaluate
from doc2data.extractors.rules import RulesExtractor
from doc2data.pdf_text import extract_text
from doc2data.pipeline import ingest_file
from doc2data.samples import GROUND_TRUTH_FILE

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
SCANS = SAMPLES / "scans"


def test_the_scans_have_no_text_layer_and_reuse_the_sample_labels():
    labels = json.loads((SCANS / GROUND_TRUTH_FILE).read_text())
    originals = json.loads((SAMPLES / GROUND_TRUTH_FILE).read_text())
    assert sorted(labels) == sorted(path.name for path in SCANS.glob("*.pdf"))
    assert {entry["layout"] for entry in labels.values()} == {"eu", "freelance", "pk", "uk", "us"}
    for name, entry in labels.items():
        assert entry == originals[name]
        assert extract_text((SCANS / name).read_bytes()) == ("", 1)


def test_a_scan_the_rules_cannot_read_is_never_scored_as_accepted():
    report = evaluate.run(SCANS, RulesExtractor())
    assert len(report.documents) == 5
    assert all(document.flagged for document in report.documents)
    assert report.silent_errors == []
    assert report.to_dict()["fully_correct"] == 0


def test_the_pipeline_sends_such_a_scan_to_review_with_the_reason(session):
    document = ingest_file(session, SCANS / "uk-02.pdf", RulesExtractor()).document
    assert document.status == "needs_review"
    assert {"missing_vendor_name", "missing_invoice_number", "missing_total"} <= {i.code for i in document.issues}
