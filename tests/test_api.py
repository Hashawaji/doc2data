import pytest
from fastapi.testclient import TestClient

from conftest import TruthExtractor
from doc2data.web.app import create_app


@pytest.fixture
def client(tmp_path, samples_dir, truth):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'api.db'}",
        extractor=TruthExtractor(samples_dir, truth),
    )
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client


def upload(client, samples_dir, file):
    with (samples_dir / file).open("rb") as handle:
        return client.post("/upload", files={"file": (file, handle, "application/pdf")})


def test_health(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_empty_dashboard_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Nothing has been read yet" in response.text


def test_upload_reads_the_invoice_and_shows_it(client, samples_dir, truth):
    response = upload(client, samples_dir, "us-01.pdf")
    assert response.status_code == 303
    detail = client.get(response.headers["location"])
    assert detail.status_code == 200
    assert truth["us-01.pdf"]["invoice"]["invoice_number"] in detail.text
    assert "accepted" in detail.text

    overview = client.get("/")
    assert "documents read" in overview.text
    assert truth["us-01.pdf"]["invoice"]["vendor_name"] in overview.text


def test_flagged_document_appears_in_review_and_can_be_accepted(client, samples_dir):
    upload(client, samples_dir, "us-03.pdf")
    review = client.get("/review")
    assert "us-03.pdf" in review.text and "Subtotal plus tax" in review.text

    assert client.get("/api/invoices", params={"status": "needs_review"}).json()[0]["filename"] == "us-03.pdf"
    assert client.post("/documents/1/approve").status_code == 303
    assert "The queue is empty" in client.get("/review").text
    assert client.get("/api/stats").json()["accepted"] == 1


def test_api_and_csv_export(client, samples_dir, truth):
    upload(client, samples_dir, "pk-01.pdf")
    invoices = client.get("/api/invoices").json()
    assert invoices[0]["invoice"]["total"] == truth["pk-01.pdf"]["invoice"]["total"]
    assert invoices[0]["invoice"]["currency"] == "PKR"

    export = client.get("/export.csv")
    assert export.headers["content-type"].startswith("text/csv")
    header, row = export.text.strip().splitlines()
    assert header.startswith("document_id,filename,status,vendor_name")
    assert "pk-01.pdf" in row


def test_upload_rejects_files_that_are_not_pdfs(client):
    response = client.post("/upload", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert response.status_code == 415


def test_unknown_document_is_a_404(client):
    assert client.get("/documents/42").status_code == 404
    assert client.post("/documents/42/approve").status_code == 404


def make_client(tmp_path, extractor, **options):
    app = create_app(database_url=f"sqlite:///{tmp_path / 'extra.db'}", extractor=extractor, **options)
    return TestClient(app, follow_redirects=False)


def test_posts_from_another_site_are_refused(client, samples_dir):
    upload(client, samples_dir, "us-03.pdf")
    evil = client.post("/documents/1/approve", headers={"origin": "https://evil.example"})
    assert evil.status_code == 403
    fetch_metadata = client.post("/documents/1/approve", headers={"sec-fetch-site": "cross-site"})
    assert fetch_metadata.status_code == 403
    assert "us-03.pdf" in client.get("/review").text  # still waiting for review

    assert client.post("/documents/1/approve", headers={"origin": "null"}).status_code == 403
    assert client.post("/documents/1/approve", headers={"sec-fetch-site": "same-site"}).status_code == 403

    same_site = client.post("/documents/1/approve", headers={"origin": "http://testserver"})
    assert same_site.status_code == 303


@pytest.mark.parametrize(
    "headers",
    [
        {},  # not a browser: curl, scripts
        {"sec-fetch-site": "same-origin"},
        {"sec-fetch-site": "none"},
        # Behind a reverse proxy the Host header is the upstream address; the browser's label decides.
        {"sec-fetch-site": "same-origin", "origin": "https://invoices.example.com"},
        {"origin": "http://testserver:80"},  # default port spelled out
        {"origin": "http://TestServer"},  # host names are case-insensitive
        {"origin": "https://invoices.example.com", "x-forwarded-host": "invoices.example.com"},
        {"origin": "https://invoices.example.com", "x-forwarded-host": "invoices.example.com, edge.internal"},
    ],
)
def test_same_site_posts_are_accepted(client, samples_dir, headers):
    upload(client, samples_dir, "us-03.pdf")
    assert client.post("/documents/1/approve", headers=headers).status_code == 303


def test_chunked_uploads_are_refused_before_the_body_is_read(client, samples_dir):
    with (samples_dir / "us-01.pdf").open("rb") as handle:
        response = client.post(
            "/upload", files={"file": ("us-01.pdf", handle, "application/pdf")},
            # Both headers together is the bypass: a small declared length, a chunked body.
            headers={"transfer-encoding": "chunked", "content-length": "100"},
        )
    assert response.status_code == 411
    assert client.get("/api/invoices").json() == []


def test_upload_does_not_run_on_the_event_loop(client):
    import inspect

    route = next(route for route in client.app.routes if getattr(route, "path", "") == "/upload")
    assert not inspect.iscoroutinefunction(route.endpoint)  # so FastAPI runs it in a worker thread


def test_oversized_uploads_are_refused(tmp_path, samples_dir, truth):
    extractor = TruthExtractor(samples_dir, truth)
    with make_client(tmp_path, extractor, max_upload_bytes=1000) as small:
        # Far over the limit: stopped from the Content-Length header, before the body is parsed.
        huge = small.post("/upload", files={"file": ("big.pdf", b"%PDF-" + b"0" * 20_000, "application/pdf")})
        assert (huge.status_code, huge.json()["detail"]) == (413, "Upload is too large.")
        # Just over the limit: within the header allowance, so stopped when the file is read.
        over = small.post("/upload", files={"file": ("over.pdf", b"%PDF-" + b"0" * 1_200, "application/pdf")})
        assert (over.status_code, over.json()["detail"]) == (413, "File is too large.")
        assert small.get("/api/invoices").json() == []


def test_missing_values_are_shown_blank_not_as_none(tmp_path, samples_dir):
    from conftest import FakeExtractor
    from doc2data.schema import Invoice

    with make_client(tmp_path, FakeExtractor(Invoice(total="10.00"))) as bare:
        upload(bare, samples_dir, "us-01.pdf")
        assert ">None<" not in bare.get("/documents").text
        assert ">None<" not in bare.get("/").text


def test_whole_quantities_are_shown_plainly(client, samples_dir, truth):
    quantities = {item["quantity"] for item in truth["pk-01.pdf"]["invoice"]["line_items"]}
    assert "20" in quantities  # Decimal("20").normalize() prints as 2E+1 unless formatted
    response = upload(client, samples_dir, "pk-01.pdf")
    detail = client.get(response.headers["location"]).text
    assert '<td class="num">20</td>' in detail and "E+" not in detail
    assert client.get("/api/invoices").json()[0]["invoice"]["line_items"][-1]["quantity"] == "20"


def test_csv_export_neutralises_spreadsheet_formulas(tmp_path, samples_dir):
    from conftest import FakeExtractor
    from doc2data.schema import Invoice

    hostile = Invoice(vendor_name='=HYPERLINK("http://evil.example","pay here")', invoice_number="+1", total="-5.00")
    with make_client(tmp_path, FakeExtractor(hostile)) as hostile_client:
        upload(hostile_client, samples_dir, "us-01.pdf")
        row = hostile_client.get("/export.csv").text.strip().splitlines()[1]
    assert "'=HYPERLINK" in row and ",'+1," in row
    assert ",-5.00," in row  # a real negative amount stays a number


def test_the_dashboard_says_when_a_reread_failed(tmp_path, samples_dir, good_invoice):
    from conftest import FakeExtractor
    from doc2data.extractors.base import ExtractionError

    database_url = f"sqlite:///{tmp_path / 'shared.db'}"
    working = TestClient(create_app(database_url=database_url, extractor=FakeExtractor(good_invoice)), follow_redirects=False)
    assert upload(working, samples_dir, "us-01.pdf").headers["location"] == "/documents/1"

    broken_extractor = FakeExtractor(error=ExtractionError("HTTP 429"))
    broken_extractor.name = "another-extractor"  # a different extractor, so the file is read again
    broken = TestClient(create_app(database_url=database_url, extractor=broken_extractor), follow_redirects=False)
    response = upload(broken, samples_dir, "us-01.pdf")
    assert response.headers["location"] == "/documents/1?reread=failed"
    page = broken.get(response.headers["location"]).text
    assert "the earlier result below was kept" in page and "accepted" in page
    assert "the earlier result below was kept" not in broken.get("/documents/1").text


def test_an_extractor_that_cannot_be_built_is_a_503_with_the_reason(tmp_path, samples_dir, monkeypatch):
    monkeypatch.setenv("DOC2DATA_EXTRACTOR", "Gemni")  # a typo in the setting
    with make_client(tmp_path, None) as misconfigured:
        assert misconfigured.get("/").status_code == 200  # the dashboard still loads
        response = upload(misconfigured, samples_dir, "us-01.pdf")
        assert response.status_code == 503 and "Unknown extractor 'Gemni'" in response.json()["detail"]
        assert misconfigured.get("/api/invoices").json() == []
