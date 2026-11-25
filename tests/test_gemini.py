"""The Gemini extractor, tested against a fake client. No network and no API key."""

import json
import sys
from decimal import Decimal
from types import ModuleType, SimpleNamespace

import pytest

from doc2data.extractors import gemini
from doc2data.extractors.base import ExtractionError
from doc2data.extractors.gemini import GeminiExtractor
from doc2data.schema import INVOICE_JSON_SCHEMA

PAYLOAD = {
    "vendor_name": "Harrow & Pike Ltd",
    "invoice_number": "2026/0451",
    "invoice_date": "2026-10-14",
    "currency": "gbp",
    "subtotal": 3520.0,
    "tax": 704,
    "total": 4224.0,
    "line_items": [{"description": "Support hours", "quantity": 2, "unit_price": 30, "amount": 60}],
}


class ApiError(Exception):
    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.code = code


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, **request):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def reply(payload=PAYLOAD, usage=None, **overrides):
    usage = usage or SimpleNamespace(prompt_token_count=1200, candidates_token_count=300, total_token_count=1500)
    text = payload if isinstance(payload, str) else json.dumps(payload)
    values = {"text": text, "usage_metadata": usage}
    return SimpleNamespace(**{**values, **overrides})


def extractor(client):
    return GeminiExtractor("gemini-3.5-flash-lite", client=client)


def test_sends_the_pdf_and_the_schema_then_parses_the_reply():
    client = FakeClient(reply())
    result = extractor(client).extract(text="ignored", pdf_bytes=b"%PDF-1.7 fake")

    request = client.requests[0]
    assert request["model"] == "gemini-3.5-flash-lite"
    document, instructions = request["contents"]
    assert document == {"inline_data": {"data": b"%PDF-1.7 fake", "mime_type": "application/pdf"}}
    assert "never guess" in instructions["text"]
    assert request["config"] == {
        "response_mime_type": "application/json", "response_json_schema": INVOICE_JSON_SCHEMA,
    }

    assert result.invoice.total == Decimal("4224.00")
    assert result.invoice.currency == "GBP"
    assert result.invoice.due_date is None
    assert (result.usage.input_tokens, result.usage.output_tokens) == (1200, 300)
    assert result.model == "gemini-3.5-flash-lite"
    assert json.loads(result.raw_output) == PAYLOAD


def test_falls_back_to_text_when_no_pdf_is_given():
    client = FakeClient(reply())
    extractor(client).extract(text="Invoice 0031 ...")
    (part,) = client.requests[0]["contents"]
    assert "never guess" in part["text"] and part["text"].endswith("Invoice 0031 ...")


def test_json_numbers_are_read_as_decimals_not_floats():
    # A float keeps about 17 significant digits; this quantity has 19 and must survive intact.
    raw = '{"line_items": [{"description": "x", "quantity": 0.1234567890123456789}], "total": 1234567.89}'
    invoice = extractor(FakeClient(reply(raw))).extract(text="x").invoice
    assert invoice.line_items[0].quantity == Decimal("0.1234567890123456789")
    assert invoice.total == Decimal("1234567.89")


def test_reasoning_tokens_are_billed_as_output():
    usage = SimpleNamespace(
        prompt_token_count=1200, candidates_token_count=300, thoughts_token_count=50, total_token_count=1550
    )
    result = extractor(FakeClient(reply(usage=usage))).extract(text="x")
    assert (result.usage.input_tokens, result.usage.output_tokens) == (1200, 350)


@pytest.mark.parametrize("usage", [SimpleNamespace(), SimpleNamespace(prompt_token_count=None)])
def test_missing_usage_counts_as_zero(usage):
    result = extractor(FakeClient(reply(usage=usage))).extract(text="x")
    assert (result.usage.input_tokens, result.usage.output_tokens) == (0, 0)
    result = extractor(FakeClient(reply(usage_metadata=None))).extract(text="x")
    assert (result.usage.input_tokens, result.usage.output_tokens) == (0, 0)


@pytest.mark.parametrize("code", [400, 429, 503])
def test_an_api_error_is_reported_once_and_not_retried_here(code):
    # Retries belong to the SDK client; a second loop here would multiply the requests.
    client = FakeClient(ApiError(code), reply())
    with pytest.raises(ExtractionError, match=f"HTTP {code}"):
        extractor(client).extract(text="", pdf_bytes=b"%PDF-")
    assert len(client.requests) == 1


def test_a_reply_that_cannot_be_read_is_an_extraction_error():
    class Unreadable:
        @property
        def text(self):
            raise RuntimeError("no usable part")

    with pytest.raises(ExtractionError, match="no usable part"):
        extractor(FakeClient(Unreadable())).extract(text="x")


def test_an_empty_reply_says_why():
    def named(name):
        return SimpleNamespace(name=name)

    blocked = reply(text=None, prompt_feedback=SimpleNamespace(block_reason=named("SAFETY")))
    with pytest.raises(ExtractionError, match=r"no text \(prompt blocked: SAFETY\)"):
        extractor(FakeClient(blocked)).extract(text="x")

    cut_off = reply(text="", candidates=[SimpleNamespace(finish_reason=named("MAX_TOKENS"))])
    with pytest.raises(ExtractionError, match=r"no text \(finish reason: MAX_TOKENS\)"):
        extractor(FakeClient(cut_off)).extract(text="x")

    plain_string = reply(text=None, candidates=[SimpleNamespace(finish_reason="RECITATION")])
    with pytest.raises(ExtractionError, match="finish reason: RECITATION"):
        extractor(FakeClient(plain_string)).extract(text="x")

    with pytest.raises(ExtractionError, match="no reason given"):
        extractor(FakeClient(reply(text=None))).extract(text="x")


def test_a_malformed_reply_is_an_extraction_error():
    with pytest.raises(ExtractionError, match="did not match"):
        extractor(FakeClient(reply("not json"))).extract(text="x")
    with pytest.raises(ExtractionError, match="did not match"):
        extractor(FakeClient(reply({"line_items": [], "total": "abc"}))).extract(text="x")
    for not_a_number in ('{"line_items": [], "total": NaN}', '{"line_items": [], "total": "NaN"}',
                         '{"line_items": [{"description": "x", "quantity": Infinity}]}'):
        with pytest.raises(ExtractionError, match="did not match"):
            extractor(FakeClient(reply(not_a_number))).extract(text="x")


def test_its_own_client_is_created_with_retries_and_a_timeout(monkeypatch):
    # This route does not retry unless told to, so losing these options would go unnoticed.
    created = {}

    def fake_client(**kwargs):
        created.update(kwargs)
        return FakeClient(reply())

    google, genai = ModuleType("google"), ModuleType("google.genai")
    genai.Client = fake_client
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)

    built = GeminiExtractor("gemini-3.5-flash-lite")
    options = created["http_options"]
    assert options["retry_options"]["attempts"] > 1
    assert options["timeout"] == gemini.REQUEST_TIMEOUT_MS > 0
    assert built.extract(text="x").invoice.invoice_number == "2026/0451"  # and it uses that client


def test_a_client_that_cannot_start_is_an_extraction_error(monkeypatch):
    def no_key(**kwargs):
        raise ValueError("Missing key inputs argument!")

    google, genai = ModuleType("google"), ModuleType("google.genai")
    genai.Client = no_key
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    with pytest.raises(ExtractionError, match="Is GEMINI_API_KEY set"):
        GeminiExtractor("gemini-3.5-flash-lite")


def test_the_installed_sdk_accepts_the_request_and_the_client_options():
    # Runs only where google-genai is installed. Catches a renamed option without any network call.
    types = pytest.importorskip("google.genai.types")
    request = extractor(FakeClient())._request(text="x", pdf_bytes=b"%PDF-1.7 fake")

    config = types.GenerateContentConfig(**request["config"])
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == INVOICE_JSON_SCHEMA
    document, instructions = (types.Part(**part) for part in request["contents"])
    assert document.inline_data.data == b"%PDF-1.7 fake"
    assert document.inline_data.mime_type == "application/pdf"
    assert "never guess" in instructions.text

    options = types.HttpOptions(**gemini.HTTP_OPTIONS)
    assert options.timeout == gemini.REQUEST_TIMEOUT_MS
    assert options.retry_options.attempts == gemini.HTTP_OPTIONS["retry_options"]["attempts"]


def test_the_timeout_is_given_in_milliseconds():
    assert gemini.REQUEST_TIMEOUT_MS >= 10_000  # 60 would mean 60 ms, not a minute


def test_a_missing_sdk_says_how_to_install_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "google", None)  # makes "from google import genai" fail
    with pytest.raises(ExtractionError, match=r"pip install 'doc2data\[gemini\]'"):
        GeminiExtractor("gemini-3.5-flash-lite")
