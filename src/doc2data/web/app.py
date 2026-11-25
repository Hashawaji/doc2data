"""Dashboard and JSON API over the ingested invoices."""

from __future__ import annotations

import io
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .. import config
from ..db import ACCEPTED, FAILED, NEEDS_REVIEW, Document, InvoiceRow, init_db, make_engine
from ..export import write_csv
from ..extractors import ExtractionError, Extractor, get_extractor
from ..pipeline import approve, ingest_bytes

HERE = Path(__file__).parent
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MULTIPART_OVERHEAD = 4096  # boundaries and part headers around the file itself
WITH_DETAILS = (
    selectinload(Document.invoice).selectinload(InvoiceRow.line_items),
    selectinload(Document.issues),
)


def _money(value: Decimal | None) -> str:
    return "" if value is None else f"{value:,.2f}"


def _quantity(value: Decimal | None) -> str:
    return "" if value is None else format(value.normalize(), "f")


def _cost(value: Decimal | None) -> str:
    return "not priced" if value is None else f"${value}"


def _utc(value: datetime | None) -> str:
    """Timestamps are stored in UTC; a database may hand them back in its session time zone."""
    if value is None:
        return ""
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%d %H:%M")


DEFAULT_PORTS = {"http": 80, "https": 443}


def _host_and_port(value: str, scheme: str) -> tuple[str, int | None] | None:
    try:
        parts = urlsplit(value if "//" in value else f"//{value}")
        if not parts.hostname:
            return None
        return parts.hostname.lower(), parts.port or DEFAULT_PORTS.get(scheme)
    except ValueError:
        return None


def is_cross_site(request: Request) -> bool:
    """True when a browser says the request was made by another site.

    Browsers label every request with Sec-Fetch-Site, which no page can forge, so it
    decides the matter when present. It also keeps working behind a reverse proxy,
    where the Host header no longer matches the public address. Without it (older
    browsers), fall back to comparing Origin with Host. A request with neither header
    does not come from a browser form and is allowed.
    """
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None:
        return fetch_site not in ("same-origin", "none")
    origin = request.headers.get("origin")
    if origin is None:
        return False
    scheme = urlsplit(origin).scheme
    source = _host_and_port(origin, scheme)
    forwarded = request.headers.get("x-forwarded-host", "")  # may list one host per proxy
    hosts = [request.headers.get("host", ""), *forwarded.split(",")]
    return source is None or all(_host_and_port(host.strip(), scheme) != source for host in hosts if host.strip())


def _document_json(document: Document) -> dict:
    invoice = document.invoice
    return {
        "id": document.id,
        "filename": document.filename,
        "status": document.status,
        "extractor": document.extractor,
        "model": document.model,
        "cost_usd": None if document.cost_usd is None else str(document.cost_usd),
        "error": document.error,
        "issues": [{"severity": i.severity, "code": i.code, "message": i.message} for i in document.issues],
        "invoice": None if invoice is None else {
            "vendor_name": invoice.vendor_name,
            "customer_name": invoice.customer_name,
            "invoice_number": invoice.invoice_number,
            "invoice_date": invoice.invoice_date.isoformat() if invoice.invoice_date else None,
            "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
            "currency": invoice.currency,
            "subtotal": None if invoice.subtotal is None else str(invoice.subtotal),
            "tax": None if invoice.tax is None else str(invoice.tax),
            "total": None if invoice.total is None else str(invoice.total),
            "line_items": [
                {
                    "description": item.description,
                    "quantity": None if item.quantity is None else _quantity(item.quantity),
                    "unit_price": None if item.unit_price is None else str(item.unit_price),
                    "amount": None if item.amount is None else str(item.amount),
                }
                for item in invoice.line_items
            ],
        },
    }


def _stats(session: Session) -> dict:
    by_status = dict(session.execute(select(Document.status, func.count()).group_by(Document.status)).all())
    accepted = session.execute(
        select(InvoiceRow.vendor_name, InvoiceRow.currency, InvoiceRow.invoice_date, InvoiceRow.total)
        .join(Document)
        .where(Document.status == ACCEPTED, InvoiceRow.total.is_not(None))
    ).all()
    by_currency: dict[str, dict] = defaultdict(lambda: {"count": 0, "total": Decimal("0")})
    by_vendor: dict[tuple, dict] = defaultdict(lambda: {"count": 0, "total": Decimal("0")})
    by_month: dict[tuple, Decimal] = defaultdict(Decimal)
    for vendor, currency, invoice_date, total in accepted:
        currency = currency or "?"
        by_currency[currency]["count"] += 1
        by_currency[currency]["total"] += total
        by_vendor[(vendor or "Unknown", currency)]["count"] += 1
        by_vendor[(vendor or "Unknown", currency)]["total"] += total
        if invoice_date:
            by_month[(invoice_date.strftime("%Y-%m"), currency)] += total
    tokens_in, tokens_out, cost, latency = session.execute(
        select(
            func.coalesce(func.sum(Document.input_tokens), 0),
            func.coalesce(func.sum(Document.output_tokens), 0),
            func.coalesce(func.sum(Document.cost_usd), 0),
            func.coalesce(func.avg(Document.latency_ms), 0),
        )
    ).one()
    unpriced = session.scalar(
        select(func.count()).select_from(Document).where(Document.model.is_not(None), Document.cost_usd.is_(None))
    )
    return {
        "documents": sum(by_status.values()),
        "accepted": by_status.get(ACCEPTED, 0),
        "needs_review": by_status.get(NEEDS_REVIEW, 0),
        "failed": by_status.get(FAILED, 0),
        "by_currency": [
            {"currency": c, "count": v["count"], "total": v["total"]} for c, v in sorted(by_currency.items())
        ],
        "by_vendor": sorted(
            ({"vendor": k[0], "currency": k[1], "count": v["count"], "total": v["total"]} for k, v in by_vendor.items()),
            key=lambda row: (row["currency"], -row["total"]),
        ),
        "by_month": [{"month": k[0], "currency": k[1], "total": v} for k, v in sorted(by_month.items())],
        "input_tokens": int(tokens_in),
        "output_tokens": int(tokens_out),
        "cost_usd": Decimal(str(cost)).quantize(Decimal("0.000001")),
        "unpriced_documents": unpriced,
        "average_latency_ms": int(latency),
    }


def create_app(
    database_url: str | None = None,
    extractor: Extractor | None = None,
    max_upload_bytes: int = MAX_UPLOAD_BYTES,
) -> FastAPI:
    settings = config.load()
    session_factory = init_db(make_engine(database_url or settings.database_url))
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.filters.update(money=_money, quantity=_quantity, cost=_cost, utc=_utc)
    state: dict[str, Extractor | None] = {"extractor": extractor}

    app = FastAPI(title="doc2data", docs_url="/api/docs", redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @app.middleware("http")
    async def guard_posts(request: Request, call_next):
        """Runs before any request body is read."""
        if request.method == "POST":
            # There is no login, so refuse form posts made from other sites (CSRF).
            if is_cross_site(request):
                return JSONResponse({"detail": "Cross-site requests are not accepted."}, status_code=403)
            if request.url.path == "/upload":
                # Chunked bodies carry no trustworthy length, so they are not accepted.
                length = request.headers.get("content-length", "")
                if "transfer-encoding" in request.headers or not length.isdigit():
                    return JSONResponse({"detail": "Content-Length is required."}, status_code=411)
                if int(length) > max_upload_bytes + MULTIPART_OVERHEAD:
                    return JSONResponse({"detail": "Upload is too large."}, status_code=413)
        return await call_next(request)

    def get_session() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    def current_extractor() -> Extractor:
        if state["extractor"] is None:  # built on first use so the dashboard starts without an API key
            state["extractor"] = get_extractor(settings.extractor)
        return state["extractor"]

    def page(request: Request, session: Session, template: str, **context) -> HTMLResponse:
        review_count = session.scalar(
            select(func.count()).select_from(Document).where(Document.status.in_([NEEDS_REVIEW, FAILED]))
        )
        return templates.TemplateResponse(request, template, {"review_count": review_count, **context})

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request, session: Session = Depends(get_session)):
        recent = session.scalars(
            select(Document).options(*WITH_DETAILS).order_by(Document.id.desc()).limit(8)
        ).all()
        return page(request, session, "overview.html", stats=_stats(session), recent=recent,
                    extractor=settings.extractor)

    @app.get("/review", response_class=HTMLResponse)
    def review(request: Request, session: Session = Depends(get_session)):
        documents = session.scalars(
            select(Document).options(*WITH_DETAILS)
            .where(Document.status.in_([NEEDS_REVIEW, FAILED])).order_by(Document.id)
        ).all()
        return page(request, session, "review.html", documents=documents)

    @app.get("/documents", response_class=HTMLResponse)
    def documents(request: Request, session: Session = Depends(get_session)):
        rows = session.scalars(select(Document).options(*WITH_DETAILS).order_by(Document.id.desc())).all()
        return page(request, session, "documents.html", documents=rows)

    @app.get("/documents/{document_id}", response_class=HTMLResponse)
    def document_detail(
        document_id: int, request: Request, reread: str | None = None, session: Session = Depends(get_session)
    ):
        document = session.scalar(select(Document).options(*WITH_DETAILS).where(Document.id == document_id))
        if document is None:
            raise HTTPException(404, "No such document.")
        return page(request, session, "document.html", document=document, reread_failed=reread == "failed")

    @app.post("/documents/{document_id}/approve")
    def approve_document(document_id: int, session: Session = Depends(get_session)):
        if approve(session, document_id) is None:
            raise HTTPException(404, "No such document.")
        return RedirectResponse("/review", status_code=303)

    @app.post("/upload")
    def upload(file: UploadFile = File(...), session: Session = Depends(get_session)):
        # A plain function: FastAPI runs it in a worker thread, so a slow extraction
        # does not stall other requests.
        data = file.file.read(max_upload_bytes + 1)
        if len(data) > max_upload_bytes:
            raise HTTPException(413, "File is too large.")
        if not data.startswith(b"%PDF-"):
            raise HTTPException(415, "Only PDF files are accepted.")
        filename = Path(file.filename or "upload.pdf").name[:255]
        try:
            extractor = current_extractor()
        except ExtractionError as exc:  # for example a missing API key
            raise HTTPException(503, str(exc)) from exc
        outcome = ingest_bytes(session, data, filename, extractor)
        notice = "?reread=failed" if outcome.error else ""  # the earlier result was kept
        return RedirectResponse(f"/documents/{outcome.document.id}{notice}", status_code=303)

    @app.get("/export.csv")
    def export_csv(session: Session = Depends(get_session)) -> Response:
        buffer = io.StringIO()
        write_csv(session, buffer)
        return Response(
            buffer.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="invoices.csv"'},
        )

    @app.get("/api/invoices")
    def api_invoices(status: str | None = None, session: Session = Depends(get_session)) -> list[dict]:
        query = select(Document).options(*WITH_DETAILS).order_by(Document.id)
        if status:
            query = query.where(Document.status == status)
        return [_document_json(document) for document in session.scalars(query).all()]

    @app.get("/api/stats")
    def api_stats(session: Session = Depends(get_session)) -> dict:
        stats = _stats(session)
        for group in ("by_currency", "by_vendor", "by_month"):
            for row in stats[group]:
                row["total"] = str(row["total"])
        stats["cost_usd"] = str(stats["cost_usd"])
        return stats

    return app
