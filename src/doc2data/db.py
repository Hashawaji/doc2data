"""Storage. SQLite by default; set DOC2DATA_DATABASE_URL for PostgreSQL."""

from __future__ import annotations

import warnings
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

# SQLite has no exact decimal type: it stores NUMERIC columns as floats and SQLAlchemy rounds
# them to the declared scale on read. PostgreSQL stores them exactly.
warnings.filterwarnings("ignore", message=r".*does \*not\* support Decimal objects natively.*")

ACCEPTED = "accepted"
NEEDS_REVIEW = "needs_review"
FAILED = "failed"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Document(Base):
    """One uploaded file and how it was read."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    filename: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int]
    pages: Mapped[int] = mapped_column(default=0)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(16), index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    extractor: Mapped[str] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(64), default=None)
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), default=None)
    latency_ms: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    raw_output: Mapped[str | None] = mapped_column(Text, default=None)
    # Set while this document is flagged as a copy: the document whose invoice it repeats.
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), default=None)

    invoice: Mapped["InvoiceRow | None"] = relationship(
        back_populates="document", uselist=False, cascade="all, delete-orphan"
    )
    issues: Mapped[list["IssueRow"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="IssueRow.id"
    )


class InvoiceRow(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), unique=True)
    vendor_name: Mapped[str | None] = mapped_column(String(255))
    vendor_key: Mapped[str | None] = mapped_column(String(255), index=True)
    customer_name: Mapped[str | None] = mapped_column(String(255))
    invoice_number: Mapped[str | None] = mapped_column(String(64), index=True)
    invoice_date: Mapped[date | None]
    due_date: Mapped[date | None]
    currency: Mapped[str | None] = mapped_column(String(3))
    subtotal: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    tax: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    total: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))

    document: Mapped[Document] = relationship(back_populates="invoice")
    line_items: Mapped[list["LineItemRow"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="LineItemRow.position"
    )


class LineItemRow(Base):
    __tablename__ = "line_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    position: Mapped[int]
    description: Mapped[str] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))

    invoice: Mapped[InvoiceRow] = relationship(back_populates="line_items")


class IssueRow(Base):
    __tablename__ = "issues"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    severity: Mapped[str] = mapped_column(String(16))
    code: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)

    document: Mapped[Document] = relationship(back_populates="issues")


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_pre_ping=True)


def init_db(engine: Engine) -> sessionmaker:
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)
