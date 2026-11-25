"""Model-based extractor using the Gemini API (google-genai SDK, generate_content).

The PDF itself is sent, so scanned pages work as well as text PDFs. The model must
answer with JSON that follows INVOICE_JSON_SCHEMA.

Retries are set once, on the SDK client, which does not retry on this route unless
told to. Rate limits and server errors (408, 429, 500, 502, 503, 504), timeouts and
refused connections are retried with exponential backoff: six attempts, about a minute
of waiting in all, which is enough to outlast a per-minute rate limit. A connection
that drops in the middle of a reply is not retried: the document is stored as failed
and is read again the next time it is submitted. The extractor adds no loop of its
own, so the two cannot multiply. An attempt that goes silent for REQUEST_TIMEOUT_MS is
cut off, so a stalled call cannot hold an upload open for ever.

The free tier is fine for the bundled synthetic samples. Google states that free-tier
prompts and responses may be used to improve its products and may be read by human
reviewers, so real client documents belong on a paid key.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Any

from .. import config
from ..schema import INVOICE_JSON_SCHEMA, Invoice
from .base import ExtractionError, ExtractionResult, Usage

REQUEST_TIMEOUT_MS = 60_000  # per attempt
HTTP_OPTIONS: dict[str, Any] = {
    "timeout": REQUEST_TIMEOUT_MS,
    # The first call plus five retries, waiting about 2, 4, 8, 16 and 32 seconds.
    "retry_options": {"attempts": 6, "initial_delay": 2.0},
}

INSTRUCTIONS = """You are reading one invoice. Return its details as JSON.

Rules:
- Report only what the document states. Leave a field out if it is not there; never guess.
- Copy amounts exactly as printed, even if they do not add up. Do not correct the arithmetic.
- Dates must be YYYY-MM-DD. Work out the day and month order from the document's country,
  language and currency, and from any date that only makes sense one way.
- currency is the ISO 4217 code.
- tax is the total tax amount. Use 0 only when the document says no tax applies.
- line_items lists each billed item in order, with quantity, unit_price and amount when shown.
"""


def _usage(response: Any) -> Usage:
    """Billable tokens. Whatever the call consumed beyond its input (answer, reasoning) is output."""
    usage = getattr(response, "usage_metadata", None)
    input_tokens = getattr(usage, "prompt_token_count", None) or 0
    output_tokens = getattr(usage, "candidates_token_count", None) or 0
    total_tokens = getattr(usage, "total_token_count", None) or 0
    return Usage(input_tokens=input_tokens, output_tokens=max(output_tokens, total_tokens - input_tokens))


def _why_empty(response: Any) -> str:
    """The API's own reason for an empty reply: a blocked prompt, or why the answer stopped."""
    def label(value: Any) -> str:
        return getattr(value, "name", None) or str(value)

    blocked = getattr(getattr(response, "prompt_feedback", None), "block_reason", None)
    if blocked:
        return f"prompt blocked: {label(blocked)}"
    for candidate in getattr(response, "candidates", None) or []:
        if finished := getattr(candidate, "finish_reason", None):
            return f"finish reason: {label(finished)}"
    return "no reason given"


class GeminiExtractor:
    name = "gemini"

    def __init__(self, model: str | None = None, *, client: Any = None) -> None:
        self.model = model or config.load().gemini_model
        if client is None:
            try:
                from google import genai
            except ImportError as exc:
                raise ExtractionError(
                    "The Gemini extractor needs the google-genai package: pip install 'doc2data[gemini]'"
                ) from exc
            try:
                client = genai.Client(http_options=HTTP_OPTIONS)  # reads GEMINI_API_KEY or GOOGLE_API_KEY
            except Exception as exc:
                raise ExtractionError(
                    f"Could not start the Gemini client. Is GEMINI_API_KEY set? ({exc})"
                ) from exc
        self._client = client

    def _request(self, *, text: str, pdf_bytes: bytes | None) -> dict[str, Any]:
        if pdf_bytes is not None:
            contents = [
                {"inline_data": {"data": pdf_bytes, "mime_type": "application/pdf"}},
                {"text": INSTRUCTIONS},
            ]
        else:
            contents = [{"text": f"{INSTRUCTIONS}\nInvoice text:\n{text}"}]
        return {
            "model": self.model,
            "contents": contents,
            "config": {
                "response_mime_type": "application/json",
                "response_json_schema": INVOICE_JSON_SCHEMA,
            },
        }

    def extract(self, *, text: str, pdf_bytes: bytes | None = None) -> ExtractionResult:
        started = time.perf_counter()
        try:
            response = self._client.models.generate_content(**self._request(text=text, pdf_bytes=pdf_bytes))
            raw = response.text
        except Exception as exc:  # the SDK has already retried what can be retried
            raise ExtractionError(f"Gemini request failed: {exc}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        if not raw:
            raise ExtractionError(f"Gemini returned no text ({_why_empty(response)}).")
        try:
            # parse_float keeps amounts exact: 1234.10 stays 1234.10, never a binary float.
            invoice = Invoice.model_validate(json.loads(raw, parse_float=Decimal))
        except (ValueError, ArithmeticError) as exc:  # bad JSON, a schema mismatch, or NaN
            raise ExtractionError(f"Gemini output did not match the invoice schema: {exc}") from exc

        return ExtractionResult(
            invoice=invoice,
            extractor=self.name,
            model=self.model,
            usage=_usage(response),
            latency_ms=latency_ms,
            raw_output=raw,
        )
