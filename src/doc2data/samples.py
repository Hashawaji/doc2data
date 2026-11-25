"""Generate the synthetic invoices and their ground truth.

Five layouts that differ the way real invoices do: date order, decimal marks, label
wording, where the vendor's name sits, and one written as prose. Two documents are
wrong on purpose (a total that does not add up; a re-issued invoice number) so the
checks have something to catch. Every name is fictional and the generator is seeded,
so the output is reproducible.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

CENT = Decimal("0.01")
SEED = 7
GROUND_TRUTH_FILE = "ground_truth.json"

CATALOGUE = [
    ("Backend API development", "45.00"), ("Data pipeline maintenance", "38.00"),
    ("Cloud infrastructure review", "60.00"), ("Database migration", "52.50"),
    ("Monthly hosting", "120.00"), ("Support hours", "30.00"),
    ("Security audit", "450.00"), ("Dashboard build", "300.00"),
    ("API integration", "275.00"), ("Load testing", "180.00"),
    ("Technical documentation", "35.00"), ("Code review", "40.00"),
]
QUANTITIES = ["1", "2", "3", "5", "8", "10", "12.5", "20"]

LAYOUTS: dict[str, dict] = {
    "us": {
        "vendors": [("Northwind Data Co.", "500 Congress Ave, Austin, TX 78701"),
                    ("Blue Heron Systems LLC", "18 Harbor Rd, Portland, ME 04101")],
        "customers": ["Acme Retail Inc.", "Lakeside Clinics Group"],
        "currency": ["USD"], "tax_rate": "0.0825", "number": "INV-104{n}", "scale": "1",
    },
    "eu": {
        "vendors": [("Kranich Software GmbH", "Hauptstrasse 5, 10115 Berlin"),
                    ("Lumen Analytics B.V.", "Keizersgracht 120, 1015 Amsterdam")],
        "customers": ["Nordlicht Handel AG", "Verde Logistics S.r.l."],
        "currency": ["EUR"], "tax_rate": "0.19", "number": "2026-041{n}", "scale": "1",
    },
    "uk": {
        "vendors": [("Harrow & Pike Ltd", "4 Canal Street, Manchester M1 3HE"),
                    ("Tidewater Digital Ltd", "22 Quay Road, Bristol BS1 4DJ")],
        "customers": ["Brightline Apps Ltd", "Fennel & Rye Foods Ltd"],
        "currency": ["GBP"], "tax_rate": "0.20", "number": "2026/045{n}", "scale": "1",
    },
    "freelance": {
        "vendors": [("Sana Qureshi", "Independent software engineer"),
                    ("Omar Haddad", "Independent data engineer")],
        "customers": ["Brightline Apps Ltd", "Acme Retail Inc."],
        "currency": ["USD"], "tax_rate": "0", "number": "003{n}", "scale": "1",
    },
    "pk": {
        "vendors": [("Indus Byte Solutions (Pvt) Ltd", "Main Boulevard, Gulberg III, Lahore"),
                    ("Falcon Gulf Technologies FZ-LLC", "Dubai Internet City, Dubai")],
        "customers": ["Sarhad Textiles Ltd", "Marina Hospitality Group"],
        "currency": ["PKR", "AED"], "tax_rate": "0.17", "number": "BL-778{n}", "scale": "250",
    },
}


@dataclass(frozen=True)
class Item:
    description: str
    quantity: Decimal
    unit_price: Decimal

    @property
    def amount(self) -> Decimal:
        return (self.quantity * self.unit_price).quantize(CENT)


@dataclass(frozen=True)
class Spec:
    file: str
    layout: str
    vendor: str
    vendor_address: str
    customer: str
    number: str
    issued: date
    due: date
    currency: str
    tax_rate: Decimal
    items: tuple[Item, ...]
    total_error: Decimal = Decimal("0")
    note: str = ""

    @property
    def subtotal(self) -> Decimal:
        return sum((item.amount for item in self.items), Decimal("0"))

    @property
    def tax(self) -> Decimal:
        return (self.subtotal * self.tax_rate).quantize(CENT)

    @property
    def total(self) -> Decimal:
        """The total as printed, which is wrong when total_error is set."""
        return self.subtotal + self.tax + self.total_error

    def truth(self) -> dict:
        prose = self.layout == "freelance"
        return {
            "vendor_name": self.vendor,
            "customer_name": self.customer,
            "invoice_number": self.number,
            "invoice_date": self.issued.isoformat(),
            "due_date": self.due.isoformat(),
            "currency": self.currency,
            "subtotal": None if prose else str(self.subtotal),
            "tax": str(self.tax),
            "total": str(self.total),
            "line_items": [
                {
                    "description": item.description,
                    "quantity": str(item.quantity),
                    "unit_price": str(item.unit_price),
                    "amount": str(item.amount),
                }
                for item in self.items
            ],
        }


def build_specs() -> list[Spec]:
    rng = random.Random(SEED)
    specs: list[Spec] = []
    for layout, cfg in LAYOUTS.items():
        for n in range(1, 5):
            index = (n - 1) % 2
            vendor, address = cfg["vendors"][index]
            currency = cfg["currency"][index % len(cfg["currency"])]
            scale = Decimal(cfg["scale"]) if currency == "PKR" else Decimal("1")
            issued = date(2026, 9, 1) + timedelta(days=rng.randrange(0, 80))
            items = tuple(
                Item(description, Decimal(rng.choice(QUANTITIES)), Decimal(price) * scale)
                for description, price in rng.sample(CATALOGUE, k=rng.randint(2, 4))
            )
            specs.append(
                Spec(
                    file=f"{layout}-{n:02d}.pdf",
                    layout=layout,
                    vendor=vendor,
                    vendor_address=address,
                    customer=cfg["customers"][index],
                    number=cfg["number"].format(n=n),
                    issued=issued,
                    due=issued + timedelta(days=rng.choice([14, 30])),
                    currency=currency,
                    tax_rate=Decimal("0.05") if currency == "AED" else Decimal(cfg["tax_rate"]),
                    items=items,
                )
            )

    def replace(file: str, **changes) -> None:
        position = next(i for i, spec in enumerate(specs) if spec.file == file)
        specs[position] = Spec(**{**specs[position].__dict__, **changes})

    replace("us-03.pdf", total_error=Decimal("100.00"),
            note="The printed total is 100.00 more than subtotal plus tax.")
    reissued = next(spec for spec in specs if spec.file == "uk-02.pdf")
    replace("uk-04.pdf", number=reissued.number,
            note="Re-uses the invoice number of uk-02.pdf from the same vendor.")
    return specs


def _grouped(value: Decimal) -> str:
    return f"{value:,.2f}"


def _money(spec: Spec, value: Decimal) -> str:
    if spec.layout == "eu":
        swapped = _grouped(value).replace(",", "_").replace(".", ",").replace("_", ".")
        return f"{swapped} EUR"
    if spec.layout == "uk":
        return f"£{_grouped(value)}"
    if spec.layout == "pk":
        return f"AED {_grouped(value)}" if spec.currency == "AED" else f"{_grouped(value)} PKR"
    return f"${_grouped(value)}"


def _quantity(spec: Spec, value: Decimal) -> str:
    text = f"{value.normalize():f}"
    return text.replace(".", ",") if spec.layout == "eu" else text


def _date(spec: Spec, value: date) -> str:
    if spec.layout == "us":
        return value.strftime("%m/%d/%Y")
    if spec.layout == "eu":
        return value.strftime("%d.%m.%Y")
    if spec.layout == "uk":
        return value.strftime("%d/%m/%Y")
    if spec.layout == "pk":
        return value.strftime("%d-%b-%Y")
    return f"{value.day} {value.strftime('%B %Y')}"


def _render(spec: Spec, path: Path) -> None:
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    pdf = FPDF(format="A4")
    pdf.set_creation_date(datetime(2026, 11, 1, tzinfo=timezone.utc))
    pdf.set_margins(18, 18, 18)
    pdf.add_page()

    def line(text: str = "", *, size: int = 11, bold: bool = False, height: float = 6.5) -> None:
        pdf.set_font("Helvetica", "B" if bold else "", size)
        pdf.cell(0, height, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def table(header: list[str], rows: list[list[str]], widths: list[int]) -> None:
        for row_number, row in enumerate([header, *rows]):
            pdf.set_font("Helvetica", "B" if row_number == 0 else "", 10)
            for column, (text, width) in enumerate(zip(row, widths)):
                pdf.cell(width, 7, text, border="B" if row_number == 0 else 0,
                         align="L" if column == 0 or len(row) == 5 and column == 1 else "R")
            pdf.ln(7)
        pdf.ln(3)

    def totals(rows: list[tuple[str, Decimal]]) -> None:
        for label, value in rows:
            pdf.set_font("Helvetica", "B" if label == rows[-1][0] else "", 11)
            pdf.cell(120, 7, label, align="R")
            pdf.cell(54, 7, _money(spec, value), align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    item_rows = [
        [item.description, _quantity(spec, item.quantity),
         _money(spec, item.unit_price), _money(spec, item.amount)]
        for item in spec.items
    ]
    percent = f"{(spec.tax_rate * 100).normalize():f}"

    if spec.layout == "us":
        line(spec.vendor, size=16, bold=True, height=9)
        line(spec.vendor_address)
        pdf.ln(6)
        line("INVOICE", size=20, bold=True, height=11)
        line(f"Invoice #: {spec.number}")
        line(f"Date: {_date(spec, spec.issued)}")
        line(f"Due Date: {_date(spec, spec.due)}")
        line(f"Bill To: {spec.customer}")
        pdf.ln(5)
        table(["Description", "Qty", "Unit Price", "Amount"], item_rows, [84, 20, 35, 35])
        totals([("Subtotal", spec.subtotal), (f"Sales Tax ({percent}%)", spec.tax),
                ("Total Due", spec.total)])
        pdf.ln(8)
        line("Thank you for your business.")

    elif spec.layout == "eu":
        # Letterhead style: the customer block leads and the vendor is only in the footer.
        line("INVOICE", size=20, bold=True, height=11)
        pdf.ln(3)
        line(spec.customer, bold=True)
        line("Accounts payable")
        pdf.ln(5)
        line(f"Invoice number: {spec.number}")
        line(f"Invoice date: {_date(spec, spec.issued)}")
        line(f"Payment due: {_date(spec, spec.due)}")
        pdf.ln(5)
        numbered = [[str(n), *row] for n, row in enumerate(
            ([i.description, _quantity(spec, i.quantity),
              _grouped(i.unit_price).replace(",", "_").replace(".", ",").replace("_", "."),
              _grouped(i.amount).replace(",", "_").replace(".", ",").replace("_", ".")]
             for i in spec.items), start=1)]
        table(["Pos", "Description", "Quantity", "Unit price", "Amount"], numbered, [14, 80, 22, 29, 29])
        totals([("Net amount", spec.subtotal), (f"VAT {percent}%", spec.tax),
                ("Total amount", spec.total)])
        pdf.set_y(-32)
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(0, 5, f"{spec.vendor} - {spec.vendor_address} - VAT ID DE123456789", align="C")

    elif spec.layout == "uk":
        line("TAX INVOICE", size=18, bold=True, height=10)
        line(spec.vendor, size=13, bold=True, height=8)
        line(spec.vendor_address)
        line("VAT Reg No: GB 123 4567 89")
        pdf.ln(5)
        line(f"Invoice No: {spec.number}")
        line(f"Date: {_date(spec, spec.issued)}")
        line(f"Due: {_date(spec, spec.due)}")
        line(f"Bill to: {spec.customer}")
        pdf.ln(5)
        table(["Description", "Hours", "Rate", "Amount"], item_rows, [84, 20, 35, 35])
        totals([("Subtotal", spec.subtotal), (f"VAT @ {percent}%", spec.tax),
                ("Balance due", spec.total)])

    elif spec.layout == "freelance":
        line(spec.vendor, size=15, bold=True, height=9)
        line(spec.vendor_address)
        pdf.ln(5)
        line(f"Invoice {spec.number}", size=13, bold=True, height=8)
        line(f"To: {spec.customer}")
        line(f"Issued on {_date(spec, spec.issued)}. Payment due by {_date(spec, spec.due)}.")
        pdf.ln(4)
        line("Work completed:")
        for item in spec.items:
            line(f"- {item.description}: {_quantity(spec, item.quantity)} hours at "
                 f"{_money(spec, item.unit_price)} per hour = {_money(spec, item.amount)}")
        pdf.ln(3)
        line("No tax applies.")
        line(f"Total: {_money(spec, spec.total)}", bold=True)
        pdf.ln(5)
        line("Please pay by bank transfer within the period above.")

    else:  # pk
        line(spec.vendor, size=15, bold=True, height=9)
        line(spec.vendor_address)
        line("NTN: 1234567-8")
        pdf.ln(4)
        line("SALES TAX INVOICE", size=14, bold=True, height=9)
        line(f"Bill No.: {spec.number}")
        line(f"Dated: {_date(spec, spec.issued)}")
        line(f"Due on: {_date(spec, spec.due)}")
        line(f"Customer: {spec.customer}")
        pdf.ln(5)
        plain_rows = [[i.description, _quantity(spec, i.quantity), _grouped(i.unit_price),
                       _grouped(i.amount)] for i in spec.items]
        table(["Description", "Qty", "Rate", "Amount"], plain_rows, [84, 20, 35, 35])
        totals([("Sub Total", spec.subtotal), (f"Sales Tax {percent}%", spec.tax),
                ("Net Payable", spec.total)])

    pdf.output(str(path))


def generate(out_dir: Path) -> list[Spec]:
    """Write the PDFs and ground_truth.json into out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    specs = build_specs()
    for spec in specs:
        _render(spec, out_dir / spec.file)
    truth = {
        spec.file: {"layout": spec.layout, "note": spec.note, "invoice": spec.truth()}
        for spec in specs
    }
    (out_dir / GROUND_TRUTH_FILE).write_text(json.dumps(truth, indent=2) + "\n")
    return specs
