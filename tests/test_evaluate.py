from decimal import Decimal

from conftest import TruthExtractor
from doc2data import evaluate
from doc2data.extractors.base import ExtractionError, ExtractionResult
from doc2data.extractors.rules import RulesExtractor


def test_a_perfect_extractor_scores_100_percent(samples_dir, truth):
    report = evaluate.run(samples_dir, TruthExtractor(samples_dir, truth))
    assert report.accuracy() == 1.0
    assert all(document.fully_correct for document in report.documents)
    assert report.silent_errors == []
    # us-03 is wrong on paper, so it is flagged even when read perfectly.
    assert [d.file for d in report.documents if d.flagged] == ["us-03.pdf"]


def test_a_plausible_but_wrong_total_counts_as_a_silent_error(samples_dir, truth):
    class OffByTen(TruthExtractor):
        def extract(self, *, text, pdf_bytes=None):
            invoice = super().extract(text=text, pdf_bytes=pdf_bytes).invoice
            # Change the total and drop what it could be checked against: a wrong figure
            # that no arithmetic check can catch.
            wrong = invoice.model_copy(update={
                "subtotal": None, "line_items": [], "total": invoice.total + Decimal("10"),
            })
            return ExtractionResult(invoice=wrong, extractor="off-by-ten")

    report = evaluate.run(samples_dir, OffByTen(samples_dir, truth))
    assert len(report.wrong_critical) == len(report.documents)
    assert len(report.silent_errors) == len(report.documents)


def test_an_extractor_that_fails_scores_zero_and_is_flagged(samples_dir):
    class Broken:
        name = "broken"

        def extract(self, *, text, pdf_bytes=None):
            raise ExtractionError("down")

    report = evaluate.run(samples_dir, Broken())
    assert report.accuracy() == 0.0
    assert all(document.failed and document.flagged for document in report.documents)
    assert report.silent_errors == []
    # A failure is reported as a failure, with its reason, not as twenty wrong answers.
    assert len(report.to_dict()["failed_to_read"]) == 20
    markdown = evaluate.to_markdown(report)
    assert "| Documents the extractor could not read | 20 of 20 |" in markdown
    assert "- `us-01.pdf`: down" in markdown


def test_rules_baseline_report(samples_dir):
    # These are the figures quoted in the README. If one changes, update both.
    report = evaluate.run(samples_dir, RulesExtractor())
    summary = report.to_dict()
    assert summary["documents"] == 20
    assert summary["field_accuracy"] == 0.86
    assert summary["fully_correct"] == 10
    assert summary["wrong_on_a_critical_field"] == 6
    assert summary["caught_by_validation"] == 1
    assert summary["silent_errors"] == ["eu-01.pdf", "eu-02.pdf", "eu-03.pdf", "eu-04.pdf", "uk-02.pdf"]
    assert report.accuracy("total") == 1.0

    markdown = evaluate.to_markdown(report)
    assert "# Evaluation: `rules`" in markdown
    assert "| Fields correct | 86.0% |" in markdown
    assert "| Cost at list price | $0 |" in markdown
    assert "| Documents the extractor could not read | 0 of 20 |" in markdown
    assert "Could not be read" not in markdown and summary["failed_to_read"] == []


def test_an_unpriced_model_is_reported_as_not_priced(samples_dir, truth):
    from doc2data.extractors.base import Usage

    class Unpriced(TruthExtractor):
        model = "some-future-model"

        def extract(self, *, text, pdf_bytes=None):
            invoice = super().extract(text=text, pdf_bytes=pdf_bytes).invoice
            return ExtractionResult(invoice=invoice, extractor="unpriced", model=self.model, usage=Usage(900, 100))

    report = evaluate.run(samples_dir, Unpriced(samples_dir, truth))
    assert not report.priced and report.input_tokens == 18_000
    assert "| Cost at list price | not priced |" in evaluate.to_markdown(report)
    assert report.to_dict()["list_price_cost_usd"] is None


def test_a_file_that_is_not_a_pdf_is_reported_not_raised(tmp_path, samples_dir, truth):
    import json
    import shutil

    shutil.copy(samples_dir / "us-01.pdf", tmp_path / "us-01.pdf")
    (tmp_path / "saved-error-page.pdf").write_text("<html><body>404 Not Found</body></html>")
    labels = {"us-01.pdf": truth["us-01.pdf"], "saved-error-page.pdf": truth["us-02.pdf"]}
    (tmp_path / "ground_truth.json").write_text(json.dumps(labels))

    summary = evaluate.run(tmp_path, TruthExtractor(samples_dir, truth)).to_dict()
    assert summary["failed_to_read"] == ["saved-error-page.pdf"]
    assert summary["fully_correct"] == 1


def test_a_document_that_could_not_be_read_is_not_credited_to_the_checks(samples_dir, truth):
    unlucky = (samples_dir / "eu-01.pdf").read_bytes()

    class FailsOnOne(TruthExtractor):
        def extract(self, *, text, pdf_bytes=None):
            if pdf_bytes == unlucky:
                raise ExtractionError("HTTP 503\nService Unavailable " + "x" * 400)
            return super().extract(text=text, pdf_bytes=pdf_bytes)

    report = evaluate.run(samples_dir, FailsOnOne(samples_dir, truth))
    summary = report.to_dict()
    assert summary["failed_to_read"] == ["eu-01.pdf"]
    assert (summary["wrong_on_a_critical_field"], summary["caught_by_validation"]) == (0, 0)
    assert (summary["fully_correct"], summary["field_accuracy"]) == (19, 0.95)

    markdown = evaluate.to_markdown(report)
    assert "| Documents wrong on a critical field | 0 of 20 |" in markdown
    assert "| ...of which validation sent to review | 0 |" in markdown
    reason = next(line for line in markdown.splitlines() if line.startswith("- `eu-01.pdf`"))
    assert "HTTP 503 Service Unavailable" in reason and len(reason) < 330
