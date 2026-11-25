from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from conftest import FakeExtractor, TruthExtractor
from doc2data import pipeline
from doc2data.db import ACCEPTED, FAILED, NEEDS_REVIEW, Document, init_db, make_engine
from doc2data.extractors.base import ExtractionError
from doc2data.pipeline import approve, ingest_bytes, ingest_file

TODAY = date(2026, 11, 24)


def issue_codes(document) -> set[str]:
    return {issue.code for issue in document.issues}


def test_a_clean_invoice_is_accepted_and_stored(session, samples_dir, truth):
    extractor = TruthExtractor(samples_dir, truth)
    document = ingest_file(session, samples_dir / "us-01.pdf", extractor, today=TODAY).document
    assert document.status == ACCEPTED
    assert document.pages == 1
    assert document.invoice.invoice_number == truth["us-01.pdf"]["invoice"]["invoice_number"]
    assert len(document.invoice.line_items) == len(truth["us-01.pdf"]["invoice"]["line_items"])


def test_the_same_file_is_only_read_once(session, samples_dir, good_invoice):
    extractor = FakeExtractor(good_invoice)
    first = ingest_file(session, samples_dir / "us-01.pdf", extractor, today=TODAY)
    second = ingest_file(session, samples_dir / "us-01.pdf", extractor, today=TODAY)
    assert first.created and not second.created
    assert second.document.id == first.document.id
    assert extractor.calls == 1
    assert session.scalar(select(func.count()).select_from(Document)) == 1


def count(session) -> int:
    return session.scalar(select(func.count()).select_from(Document))


def test_a_failed_read_is_tried_again(session, samples_dir, good_invoice):
    path = samples_dir / "us-01.pdf"
    failed = ingest_file(session, path, FakeExtractor(error=ExtractionError("rate limited"))).document
    assert failed.status == FAILED

    retried = ingest_file(session, path, FakeExtractor(good_invoice), today=TODAY)
    assert retried.created and retried.document.status == ACCEPTED
    assert count(session) == 1


def test_a_different_extractor_reads_the_file_afresh(session, samples_dir, truth, good_invoice):
    path = samples_dir / "us-01.pdf"
    ingest_file(session, path, FakeExtractor(good_invoice), today=TODAY)
    again = ingest_file(session, path, TruthExtractor(samples_dir, truth), today=TODAY)
    assert again.created and again.document.extractor == "truth"
    assert count(session) == 1


def test_force_reads_again_and_replaces_the_result(session, samples_dir, good_invoice):
    extractor = FakeExtractor(good_invoice)
    path = samples_dir / "us-01.pdf"
    ingest_file(session, path, extractor, today=TODAY)
    forced = ingest_file(session, path, extractor, today=TODAY, force=True)
    assert forced.created and extractor.calls == 2
    assert count(session) == 1


def test_duplicate_detection_handles_non_ascii_names(session, samples_dir, good_invoice):
    first = good_invoice.model_copy(update={"vendor_name": "Ørsted Services A/S"})
    second = good_invoice.model_copy(update={"vendor_name": "ØRSTED SERVICES A/S"})
    ingest_file(session, samples_dir / "us-01.pdf", FakeExtractor(first), today=TODAY)
    reissued = ingest_file(session, samples_dir / "us-02.pdf", FakeExtractor(second), today=TODAY).document
    assert "possible_duplicate" in issue_codes(reissued)


def test_a_database_error_still_leaves_a_record(session, samples_dir, good_invoice, monkeypatch):
    commit_fails_once(session, monkeypatch)
    document = ingest_file(session, samples_dir / "us-01.pdf", FakeExtractor(good_invoice), today=TODAY).document
    assert document.status == FAILED
    assert document.error.startswith("Could not store the result")
    assert document.invoice is None and count(session) == 1


def failing(name: str, message: str) -> FakeExtractor:
    extractor = FakeExtractor(error=ExtractionError(message))
    extractor.name = name
    return extractor


def test_a_failed_reread_keeps_the_earlier_result(session, samples_dir, good_invoice):
    path = samples_dir / "us-01.pdf"
    first = ingest_file(session, path, FakeExtractor(good_invoice), today=TODAY).document

    outcome = ingest_file(session, path, failing("other", "HTTP 429"), today=TODAY)
    assert not outcome.created and outcome.error == "HTTP 429"
    assert outcome.document.id == first.id and outcome.document.status == ACCEPTED
    assert outcome.document.invoice.invoice_number == "INV-1"
    assert count(session) == 1

    forced = ingest_file(session, path, failing("fake", "HTTP 503"), today=TODAY, force=True)
    assert forced.error == "HTTP 503" and forced.document.status == ACCEPTED


def test_a_reread_keeps_the_document_id_and_clears_the_approval(session, samples_dir, truth):
    extractor = TruthExtractor(samples_dir, truth)
    path = samples_dir / "us-03.pdf"
    document = ingest_file(session, path, extractor, today=TODAY).document
    approve(session, document.id)
    assert document.status == ACCEPTED

    reread = ingest_file(session, path, extractor, today=TODAY, force=True).document
    assert reread.id == document.id
    assert reread.status == NEEDS_REVIEW and reread.reviewed_at is None
    assert len(reread.issues) == 1 and len(reread.invoice.line_items) == len(truth["us-03.pdf"]["invoice"]["line_items"])


def test_rereading_keeps_first_come_order_for_duplicates(session, samples_dir, truth):
    extractor = TruthExtractor(samples_dir, truth)
    for _ in range(2):  # the second pass re-reads both files
        original = ingest_file(session, samples_dir / "uk-02.pdf", extractor, today=TODAY, force=True).document
        reissued = ingest_file(session, samples_dir / "uk-04.pdf", extractor, today=TODAY, force=True).document
    assert original.status == ACCEPTED and issue_codes(original) == set()
    assert reissued.status == NEEDS_REVIEW
    assert f"document {original.id} (uk-02.pdf)" in reissued.issues[0].message
    assert count(session) == 2


def test_a_duplicate_is_caught_when_the_original_is_read_after_the_copy(session, samples_dir, truth):
    extractor = TruthExtractor(samples_dir, truth)
    ingest_file(session, samples_dir / "uk-02.pdf", failing("truth", "HTTP 429"))  # document 1, failed
    copy = ingest_file(session, samples_dir / "uk-04.pdf", extractor, today=TODAY).document
    retried = ingest_file(session, samples_dir / "uk-02.pdf", extractor, today=TODAY).document
    # Whichever is read successfully first is booked; the other one is flagged.
    assert copy.status == ACCEPTED
    assert retried.id == 1 and retried.status == NEEDS_REVIEW
    assert "possible_duplicate" in issue_codes(retried)


def test_an_approved_duplicate_counts_as_booked(session, samples_dir, good_invoice):
    extractor = FakeExtractor(good_invoice)
    ingest_file(session, samples_dir / "us-01.pdf", extractor, today=TODAY)
    copy = ingest_file(session, samples_dir / "us-02.pdf", extractor, today=TODAY).document
    assert "possible_duplicate" in issue_codes(copy)
    approve(session, copy.id)

    # The first document is re-read and turns out to carry a different number.
    renumbered = FakeExtractor(good_invoice.model_copy(update={"invoice_number": "INV-9"}))
    ingest_file(session, samples_dir / "us-01.pdf", renumbered, today=TODAY, force=True)

    third = ingest_file(session, samples_dir / "us-03.pdf", extractor, today=TODAY).document
    assert third.status == NEEDS_REVIEW
    assert f"document {copy.id} (us-02.pdf)" in third.issues[0].message


def commit_fails_once(session, monkeypatch, error=None):
    """The first commit that has something to store fails, as a database refusing a value does."""
    real_commit, failed = session.commit, []

    def commit():
        if not failed and (session.new or session.dirty or session.deleted):
            failed.append(1)
            raise error or SQLAlchemyError("value too long for type character varying(3)")
        real_commit()

    monkeypatch.setattr(session, "commit", commit)


def test_a_database_error_on_a_retry_updates_the_failed_record(session, samples_dir, good_invoice, monkeypatch):
    path = samples_dir / "us-01.pdf"
    ingest_file(session, path, failing("fake", "HTTP 429"))
    commit_fails_once(session, monkeypatch)
    document = ingest_file(session, path, FakeExtractor(good_invoice), today=TODAY).document
    assert document.status == FAILED and document.error.startswith("Could not store the result")
    assert count(session) == 1


def test_a_database_error_on_a_reread_keeps_the_earlier_result(session, samples_dir, good_invoice, monkeypatch):
    path = samples_dir / "us-01.pdf"
    extractor = FakeExtractor(good_invoice)
    ingest_file(session, path, extractor, today=TODAY)
    commit_fails_once(session, monkeypatch)
    outcome = ingest_file(session, path, extractor, today=TODAY, force=True)
    assert not outcome.created and outcome.error.startswith("Could not store the result")
    assert outcome.document.status == ACCEPTED and outcome.document.invoice.invoice_number == "INV-1"
    assert count(session) == 1


def test_a_document_whose_total_does_not_add_up_goes_to_review(session, samples_dir, truth):
    extractor = TruthExtractor(samples_dir, truth)
    document = ingest_file(session, samples_dir / "us-03.pdf", extractor, today=TODAY).document
    assert document.status == NEEDS_REVIEW
    assert "totals_mismatch" in issue_codes(document)


def test_a_reissued_invoice_number_is_flagged_as_a_duplicate(session, samples_dir, truth):
    extractor = TruthExtractor(samples_dir, truth)
    original = ingest_file(session, samples_dir / "uk-02.pdf", extractor, today=TODAY).document
    reissued = ingest_file(session, samples_dir / "uk-04.pdf", extractor, today=TODAY).document
    assert original.status == ACCEPTED
    assert reissued.status == NEEDS_REVIEW
    assert "possible_duplicate" in issue_codes(reissued)


def test_an_unreadable_file_is_recorded_as_failed(session, good_invoice):
    extractor = FakeExtractor(good_invoice)
    document = ingest_bytes(session, b"not a pdf", "broken.pdf", extractor).document
    assert document.status == FAILED
    assert "could not read PDF" in document.error
    assert extractor.calls == 0


def test_an_extractor_failure_is_recorded_not_raised(session, samples_dir):
    extractor = FakeExtractor(error=ExtractionError("model unavailable"))
    document = ingest_file(session, samples_dir / "us-01.pdf", extractor).document
    assert document.status == FAILED
    assert document.error == "model unavailable"
    assert document.invoice is None


def test_a_person_can_accept_a_flagged_document(session, samples_dir, truth):
    extractor = TruthExtractor(samples_dir, truth)
    document = ingest_file(session, samples_dir / "us-03.pdf", extractor, today=TODAY).document
    approved = approve(session, document.id)
    assert approved.status == ACCEPTED
    assert approved.reviewed_at is not None
    assert approve(session, 9999) is None


def test_a_copy_waiting_for_review_still_holds_its_number(session, samples_dir, good_invoice):
    extractor = FakeExtractor(good_invoice)
    ingest_file(session, samples_dir / "us-01.pdf", extractor, today=TODAY)
    copy = ingest_file(session, samples_dir / "us-02.pdf", extractor, today=TODAY).document
    # The first document is re-read and turns out to carry a different number.
    renumbered = FakeExtractor(good_invoice.model_copy(update={"invoice_number": "INV-9"}))
    ingest_file(session, samples_dir / "us-01.pdf", renumbered, today=TODAY, force=True)

    third = ingest_file(session, samples_dir / "us-03.pdf", extractor, today=TODAY).document
    assert copy.status == NEEDS_REVIEW  # nobody has looked at it yet
    assert third.status == NEEDS_REVIEW
    assert f"document {copy.id} (us-02.pdf)" in third.issues[0].message


def test_rereading_after_an_approval_flags_the_copy_again_not_the_original(session, samples_dir, good_invoice):
    extractor = FakeExtractor(good_invoice)
    original = ingest_file(session, samples_dir / "us-01.pdf", extractor, today=TODAY).document
    copy = ingest_file(session, samples_dir / "us-02.pdf", extractor, today=TODAY).document
    approve(session, copy.id)

    ingest_file(session, samples_dir / "us-01.pdf", extractor, today=TODAY, force=True)
    ingest_file(session, samples_dir / "us-02.pdf", extractor, today=TODAY, force=True)
    assert original.status == ACCEPTED and issue_codes(original) == set()
    assert copy.status == NEEDS_REVIEW and issue_codes(copy) == {"possible_duplicate"}


def test_an_original_held_for_another_reason_still_flags_its_copy(session, samples_dir, good_invoice):
    extractor = FakeExtractor(good_invoice.model_copy(update={"total": Decimal("999.00")}))
    original = ingest_file(session, samples_dir / "us-01.pdf", extractor, today=TODAY).document
    copy = ingest_file(session, samples_dir / "us-02.pdf", extractor, today=TODAY).document
    assert original.status == NEEDS_REVIEW and issue_codes(original) == {"totals_mismatch"}
    assert issue_codes(copy) == {"totals_mismatch", "possible_duplicate"}


def test_a_copy_that_is_reread_with_another_number_is_no_longer_a_copy(session, samples_dir, good_invoice):
    extractor = FakeExtractor(good_invoice)
    original = ingest_file(session, samples_dir / "us-01.pdf", extractor, today=TODAY).document
    copy = ingest_file(session, samples_dir / "us-02.pdf", extractor, today=TODAY).document
    assert copy.duplicate_of_id == original.id

    other_number = FakeExtractor(good_invoice.model_copy(update={"invoice_number": "INV-2"}))
    ingest_file(session, samples_dir / "us-02.pdf", other_number, today=TODAY, force=True)
    assert copy.status == ACCEPTED and issue_codes(copy) == set()
    assert copy.duplicate_of_id is None


def test_no_database_transaction_is_open_while_the_extractor_runs(session, samples_dir, good_invoice):
    class Watching(FakeExtractor):
        def extract(self, *, text, pdf_bytes=None):
            self.in_transaction = session.in_transaction()
            return super().extract(text=text, pdf_bytes=pdf_bytes)

    extractor = Watching(good_invoice)
    path = samples_dir / "us-01.pdf"
    ingest_file(session, path, extractor, today=TODAY)  # a new file
    assert extractor.in_transaction is False
    ingest_file(session, path, extractor, today=TODAY, force=True)  # a stored one, read again
    assert extractor.in_transaction is False


def looked_too_early(monkeypatch, times: int) -> None:
    """The next look-ups miss, as they do when another request stores the same file a moment later."""
    real_find, calls = pipeline._find, []

    def find(session, sha256):
        calls.append(sha256)
        return None if len(calls) <= times else real_find(session, sha256)

    monkeypatch.setattr(pipeline, "_find", find)


def test_the_same_file_arriving_twice_at_once_is_stored_once_and_is_not_a_failure(
    session, samples_dir, good_invoice, monkeypatch
):
    path = samples_dir / "us-01.pdf"
    first = ingest_file(session, path, FakeExtractor(good_invoice), today=TODAY).document
    looked_too_early(monkeypatch, times=1)
    outcome = ingest_file(session, path, FakeExtractor(good_invoice), today=TODAY)
    assert (outcome.document.id, outcome.created, outcome.error) == (first.id, False, None)
    assert outcome.document.status == ACCEPTED and count(session) == 1


def test_the_same_file_failing_twice_at_once_leaves_one_record(session, samples_dir, monkeypatch):
    path = samples_dir / "us-01.pdf"
    first = ingest_file(session, path, failing("fake", "HTTP 503")).document
    looked_too_early(monkeypatch, times=2)
    outcome = ingest_file(session, path, failing("fake", "HTTP 503"))
    assert (outcome.document.id, outcome.created, outcome.error) == (first.id, False, None)
    assert outcome.document.status == FAILED and count(session) == 1


def test_a_constraint_error_on_a_reread_is_reported_and_keeps_the_earlier_result(
    session, samples_dir, good_invoice, monkeypatch
):
    path = samples_dir / "us-01.pdf"
    extractor = FakeExtractor(good_invoice)
    ingest_file(session, path, extractor, today=TODAY)
    refused = IntegrityError("INSERT INTO line_items ...", {}, Exception("NOT NULL constraint failed"))
    commit_fails_once(session, monkeypatch, refused)
    outcome = ingest_file(session, path, extractor, today=TODAY, force=True)
    assert not outcome.created and outcome.error.startswith("Could not store the result")
    assert outcome.document.status == ACCEPTED and count(session) == 1


def test_a_failed_read_racing_a_good_one_reports_its_error_and_keeps_the_good_result(
    session, samples_dir, good_invoice, monkeypatch
):
    path = samples_dir / "us-01.pdf"
    good = ingest_file(session, path, FakeExtractor(good_invoice), today=TODAY).document
    looked_too_early(monkeypatch, times=2)  # neither look-up sees the row the other request stored
    outcome = ingest_file(session, path, failing("other", "HTTP 503"))
    assert (outcome.document.id, outcome.created, outcome.error) == (good.id, False, "HTTP 503")
    assert outcome.document.status == ACCEPTED and count(session) == 1


def test_documents_from_earlier_reads_stay_usable_after_the_session_closes(tmp_path, samples_dir, good_invoice):
    factory = init_db(make_engine(f"sqlite:///{tmp_path / 'closed.db'}"))
    second = FakeExtractor(good_invoice.model_copy(update={"invoice_number": "INV-2"}))
    with factory() as own_session:
        first = ingest_file(own_session, samples_dir / "us-01.pdf", FakeExtractor(good_invoice), today=TODAY).document
        ingest_file(own_session, samples_dir / "us-02.pdf", second, today=TODAY)
    assert (first.filename, first.status) == ("us-01.pdf", ACCEPTED)


def test_a_callers_pending_change_is_saved_not_discarded(session, samples_dir, good_invoice):
    first = ingest_file(session, samples_dir / "us-01.pdf", FakeExtractor(good_invoice), today=TODAY).document
    first.filename = "renamed.pdf"  # changed, not yet committed
    second = FakeExtractor(good_invoice.model_copy(update={"invoice_number": "INV-2"}))
    ingest_file(session, samples_dir / "us-02.pdf", second, today=TODAY)
    session.expire_all()
    assert session.get(Document, first.id).filename == "renamed.pdf"


def test_a_copy_is_flagged_against_the_earliest_document_with_that_number(session, samples_dir, good_invoice):
    extractor = FakeExtractor(good_invoice)
    first = ingest_file(session, samples_dir / "us-01.pdf", extractor, today=TODAY).document
    ingest_file(session, samples_dir / "us-02.pdf", extractor, today=TODAY)
    third = ingest_file(session, samples_dir / "us-03.pdf", extractor, today=TODAY).document
    assert f"document {first.id} (us-01.pdf)" in third.issues[0].message


def test_approving_an_out_of_date_duplicate_flag_does_not_hide_a_later_match(session, samples_dir, good_invoice):
    extractor = FakeExtractor(good_invoice)
    original = ingest_file(session, samples_dir / "us-01.pdf", extractor, today=TODAY).document
    copy = ingest_file(session, samples_dir / "us-02.pdf", extractor, today=TODAY).document
    renumbered = FakeExtractor(good_invoice.model_copy(update={"invoice_number": "INV-9"}))
    ingest_file(session, samples_dir / "us-01.pdf", renumbered, today=TODAY, force=True)
    approve(session, copy.id)  # its flag points at a document that no longer matches it
    assert copy.duplicate_of_id is None

    ingest_file(session, samples_dir / "us-01.pdf", extractor, today=TODAY, force=True)  # INV-1 again
    assert original.status == NEEDS_REVIEW
    assert f"document {copy.id} (us-02.pdf)" in original.issues[0].message


def test_an_approval_does_not_cover_a_match_that_returns_after_the_original_changed(
    session, samples_dir, good_invoice
):
    extractor = FakeExtractor(good_invoice)
    original = ingest_file(session, samples_dir / "us-01.pdf", extractor, today=TODAY).document
    copy = ingest_file(session, samples_dir / "us-02.pdf", extractor, today=TODAY).document
    approve(session, copy.id)  # the person saw both documents carry INV-1
    renumbered = FakeExtractor(good_invoice.model_copy(update={"invoice_number": "INV-9"}))
    ingest_file(session, samples_dir / "us-01.pdf", renumbered, today=TODAY, force=True)
    assert original.status == ACCEPTED and copy.duplicate_of_id is None

    ingest_file(session, samples_dir / "us-01.pdf", extractor, today=TODAY, force=True)  # INV-1 again
    assert original.status == NEEDS_REVIEW
    assert f"document {copy.id} (us-02.pdf)" in original.issues[0].message
