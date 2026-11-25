"""Rule-based baseline: regular expressions over the PDF's text layer.

It needs no API key, so the project runs anywhere and the tests are deterministic.
It is also the bar a model has to beat. Rules are free and instant, but they break
when wording or layout changes, and the evaluation report shows exactly where.
"""

from __future__ import annotations

import re
import time
from datetime import date
from decimal import Decimal, InvalidOperation

from ..schema import Invoice, LineItem
from .base import ExtractionResult

MONTHS = {
    name: number
    for number, names in enumerate(
        [
            ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
            ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
            ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for name in names
}

CURRENCY_CODES = re.compile(r"\b(USD|EUR|GBP|PKR|AED|CAD|AUD|INR|SAR|CHF)\b")
CURRENCY_SYMBOLS = {"$": "USD", "£": "GBP", "€": "EUR"}
# A number. Thousands grouped by spaces are joined only in the continental form with a
# decimal comma ("1 190,00"); "VAT 20 784.00" stays two numbers.
NUMBER = re.compile(r"\d{1,3}(?: \d{3})+,\d{2}(?!\d)|\d[\d.,]*\d|\d")
# A minus sign opens the value: "-$450.00" or "-450.00", not the hyphen in "555-0199".
MINUS_BEFORE = re.compile(r"(?:^|[\s$£€])[-\u2212][$£€]?$")
UNJOINED_NUMBER = re.compile(r"\d[\d.,]*\d|\d")
PLAIN_SEPARATORS = r"\s*[:#.\-]*\s*"
# Between an amount label and its value a hyphen is punctuation, unless it follows a
# space and touches the value ("Total Due -$450.00"), which makes it a minus sign.
SIGNED_SEPARATORS = r"(?:[\s:#.]|-(?![$£€\d])|(?<!\s)-)*"
TITLE_LINE = re.compile(r"^(tax\s+)?(invoice|bill|receipt)$", re.IGNORECASE)

INVOICE_NUMBER_LABELS = [
    r"invoice\s*(?:no\.?|number|num\.?|#)",
    r"inv\.?\s*(?:no\.?|#)",
    r"bill\s*(?:no\.?|number|#)",
]
INVOICE_DATE_LABELS = [r"invoice\s*date", r"date\s*of\s*issue", r"issued(?:\s*on)?", r"dated"]
GENERIC_DATE_LABEL = [r"date"]
DUE_LINE = re.compile(r"\bdue\b", re.IGNORECASE)
DUE_DATE_LABELS = [r"due\s*date", r"payment\s*due(?:\s*by)?", r"due\s*(?:on|by)", r"due"]
SUBTOTAL_LABELS = [r"sub\s*-?\s*total", r"net\s*amount", r"amount\s*before\s*tax"]
SUBTOTAL_LINE = re.compile(r"sub\s*-?\s*total", re.IGNORECASE)
TAX_LABELS = [r"sales\s*tax", r"vat", r"gst", r"tax"]
NOT_A_TAX_AMOUNT = re.compile(
    r"before\s*tax|tax\s+invoice|reg(?:istration)?\.?\s*(?:no|number)|tax\s*id", re.IGNORECASE
)
TOTAL_LABELS = [
    r"total\s*due", r"amount\s*due", r"balance\s*due", r"grand\s*total",
    r"net\s*payable", r"total\s*amount", r"total",
]
CUSTOMER_LABELS = [r"bill(?:ed)?\s*to", r"customer", r"client"]
# A header or totals line: one of these labels, then its value ("Tax 42.90",
# "Total AED 7,521.15"). "Tax return preparation 1 450.00 450.00" is not one, because a
# word follows the label. The possessive quantifiers stop the pattern from backing up
# to a position where the final check would pass by accident.
NOT_AN_ITEM = re.compile(
    r"^(?:" + "|".join(
        INVOICE_NUMBER_LABELS + INVOICE_DATE_LABELS + GENERIC_DATE_LABEL + DUE_DATE_LABELS
        + SUBTOTAL_LABELS + TAX_LABELS + TOTAL_LABELS
    ) + r")(?![A-Za-z])[\s:#.\-]*+(?:(?:USD|EUR|GBP|PKR|AED|CAD|AUD|INR|SAR|CHF)\b\s*+)?(?![A-Za-z])",
    re.IGNORECASE,
)
ITEM_TOLERANCE = Decimal("0.02")

# A price: optional currency, then a number, either in the continental form with
# thousands grouped by spaces ("1 299,00") or as one unbroken number ("1299.00").
CURRENCY_PREFIX = r"(?:[A-Z]{3}\s)?[$£€]?"
GROUPED_PRICE = CURRENCY_PREFIX + r"\d{1,3}(?: \d{3})+,\d{2}"
PLAIN_PRICE = CURRENCY_PREFIX + r"\d[\d.,]*"


def _item_shape(unit: str, amount: str) -> re.Pattern[str]:
    return re.compile(
        r"^(?P<description>\D.*?)\s+(?P<quantity>\d+(?:[.,]\d+)?)\s+"
        rf"(?P<unit>{unit})\s+(?P<amount>{amount})(?:\s[A-Z]{{3}})?$"
    )


# A line can split into description, quantity, unit price and amount in more than one
# way: "1 299,00" may be one price or two numbers, and a description may end in a
# number ("Monitor 27 2 349,00 698,00"). The splits are tried in this order, grouped
# prices first, and the first one whose arithmetic holds is taken.
ITEM_SHAPES = [
    _item_shape(unit, amount)
    for unit in (GROUPED_PRICE, PLAIN_PRICE)
    for amount in (GROUPED_PRICE, PLAIN_PRICE)
]


def _is_thousands_group(whole: str, fraction: str) -> bool:
    """With a single separator, '1.234' is one thousand; '0.125' and '1234.567' are decimals."""
    return len(fraction) == 3 and 1 <= len(whole) <= 3 and not whole.startswith("0")


def parse_amount(raw: str) -> Decimal | None:
    """Parse '1,234.56', '1.234,56', '1 234,56' or '1234.5' into a Decimal. The sign is ignored."""
    digits = re.sub(r"[^\d.,]", "", raw)
    if not any(ch.isdigit() for ch in digits):
        return None
    last_dot, last_comma = digits.rfind("."), digits.rfind(",")
    if last_dot != -1 and last_comma != -1:
        mark = "." if last_dot > last_comma else ","
    elif last_dot != -1 or last_comma != -1:
        separator = "." if last_dot != -1 else ","
        if digits.count(separator) > 1:
            mark = ""
        else:
            whole, fraction = digits.split(separator)
            mark = "" if _is_thousands_group(whole, fraction) else separator
    else:
        mark = ""
    if mark:
        whole, fraction = digits.rsplit(mark, 1)
        digits = re.sub(r"[.,]", "", whole) + "." + fraction
    else:
        digits = re.sub(r"[.,]", "", digits)
    try:
        return Decimal(digits)
    except InvalidOperation:
        return None


def parse_date(raw: str) -> date | None:
    """Find the first date in a string. Slashes are read month-first, dots and dashes day-first."""
    try:
        if m := re.search(r"(\d{4})-(\d{2})-(\d{2})", raw):
            return date(int(m[1]), int(m[2]), int(m[3]))
        if m := re.search(r"(\d{1,2})[\s-]+([A-Za-z]{3,9})\.?[\s,-]+(\d{4})", raw):
            month = MONTHS.get(m[2].lower())
            return date(int(m[3]), month, int(m[1])) if month else None
        if m := re.search(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", raw):
            month = MONTHS.get(m[1].lower())
            return date(int(m[3]), month, int(m[2])) if month else None
        if m := re.search(r"(\d{1,2})([./-])(\d{1,2})\2(\d{4})", raw):
            first, second, year = int(m[1]), int(m[3]), int(m[4])
            month, day = (first, second) if m[2] == "/" else (second, first)
            if month > 12:
                month, day = day, month
            return date(year, month, day)
    except ValueError:
        return None
    return None


def _after_label(
    lines: list[str],
    labels: list[str],
    skip: re.Pattern[str] | None = None,
    signed: bool = False,
) -> str | None:
    """Text following the first label that matches, trying labels in priority order.

    Punctuation between label and value is dropped. With signed=True (amounts) a minus
    sign in front of the value is kept.
    """
    separators = SIGNED_SEPARATORS if signed else PLAIN_SEPARATORS
    for label in labels:
        pattern = re.compile(rf"(?<![A-Za-z]){label}(?![A-Za-z]){separators}(?P<rest>.*)$", re.IGNORECASE)
        for line in lines:
            if skip is not None and skip.search(line):
                continue
            match = pattern.search(line)
            if match and match.group("rest").strip():
                return match.group("rest").strip()
    return None


def _last_amount(text: str | None, pattern: re.Pattern[str] = NUMBER) -> Decimal | None:
    """The last number on a line that is not a percentage, with its sign."""
    if not text:
        return None
    candidates = [m for m in pattern.finditer(text) if not text[m.end():].lstrip().startswith("%")]
    if not candidates:
        return None
    last = candidates[-1]
    amount = parse_amount(last.group())
    if amount is None:
        return None
    before, after = text[: last.start()], text[last.end():]
    in_brackets = before.rstrip("$£€ ").endswith("(") and after.lstrip().startswith(")")
    return -amount if MINUS_BEFORE.search(before) or in_brackets else amount


RATE_AND_AMOUNT = re.compile(r"(\d{1,2}(?:[.,]\d+)?) (\d{3},\d{2})$")


def _tax(text: str | None, subtotal: Decimal | None, total: Decimal | None) -> Decimal | None:
    """The tax amount. "VAT 20 784,00" may be twenty thousand, or a 20% rate and 784,00.

    The joined reading stands unless the invoice's other figures contradict it and
    confirm the split one: with a subtotal, the tax must equal total minus subtotal;
    without one, the split is accepted only if 784,00 really is 20% of the net.
    """
    tax = _last_amount(text)
    if text is None or tax is None or total is None:
        return tax
    if subtotal is not None:
        expected = total - subtotal
        if abs(tax - expected) <= ITEM_TOLERANCE:
            return tax
        unjoined = _last_amount(text, UNJOINED_NUMBER)
        return unjoined if unjoined is not None and abs(unjoined - expected) <= ITEM_TOLERANCE else tax
    candidates = [m for m in NUMBER.finditer(text) if not text[m.end():].lstrip().startswith("%")]
    split = RATE_AND_AMOUNT.fullmatch(candidates[-1].group())
    if split is None:
        return tax
    rate, amount = parse_amount(split[1]), parse_amount(split[2])
    if rate is None or amount is None:
        return tax
    return amount if abs((total - amount) * rate / 100 - amount) <= ITEM_TOLERANCE else tax


def _currency(text: str) -> str | None:
    if match := CURRENCY_CODES.search(text):
        return match.group(1)
    for symbol, code in CURRENCY_SYMBOLS.items():
        if symbol in text:
            return code
    return None


def _vendor(lines: list[str]) -> str | None:
    for line in lines[:6]:
        if TITLE_LINE.match(line) or ":" in line or sum(ch.isdigit() for ch in line) > 3:
            continue
        if any(ch.isalpha() for ch in line):
            return line
    return None


def _invoice_number(lines: list[str]) -> str | None:
    rest = _after_label(lines, INVOICE_NUMBER_LABELS)
    if rest is None:
        for line in lines:
            if match := re.match(r"(?i)^invoice\s+([A-Z0-9][A-Z0-9/\-]*\d)\b", line):
                return match.group(1)
        return None
    match = re.match(r"[A-Za-z0-9][A-Za-z0-9/\-]*", rest)
    return match.group(0) if match else None


def _line_items(lines: list[str]) -> tuple[list[LineItem], set[int]]:
    """The line items, and the positions of the lines they were read from.

    An item is a line shaped like description, quantity, unit price, amount whose
    arithmetic holds. Requiring quantity x unit price = amount is what keeps out lines
    that merely look the part: "VAT Reg No: GB 123 4567 89", "Total amount due
    12 345 678,90", a phone number. The cost is that an item priced with a per-line
    discount is left out. The invoice's totals are still checked, but an invoice whose
    items were all left out is stored without line items.
    """
    items: list[LineItem] = []
    used: set[int] = set()
    for index, line in enumerate(lines):
        if NOT_AN_ITEM.match(line):
            continue
        if (item := _item(line)) is not None:
            items.append(item)
            used.add(index)
    return items, used


def _item(line: str) -> LineItem | None:
    """The line read as an item: the first split in ITEM_SHAPES whose arithmetic holds."""
    for shape in ITEM_SHAPES:
        match = shape.match(line)
        if match is None:
            continue
        quantity = parse_amount(match["quantity"])
        unit_price, amount = parse_amount(match["unit"]), parse_amount(match["amount"])
        if quantity is None or unit_price is None or amount is None:
            continue
        if abs(quantity * unit_price - amount) <= ITEM_TOLERANCE:
            return LineItem(
                description=match["description"], quantity=quantity, unit_price=unit_price, amount=amount
            )
    return None


class RulesExtractor:
    name = "rules"

    def extract(self, *, text: str, pdf_bytes: bytes | None = None) -> ExtractionResult:
        started = time.perf_counter()
        lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
        line_items, item_lines = _line_items(lines)
        # A line read as an item is not searched for labels: "Tax return preparation
        # 1 450.00 450.00" is not the tax, and "Due diligence report ..." is not the due date.
        others = [line for index, line in enumerate(lines) if index not in item_lines]
        invoice_date = parse_date(_after_label(others, INVOICE_DATE_LABELS) or "") or parse_date(
            _after_label(others, GENERIC_DATE_LABEL, skip=DUE_LINE) or ""
        )
        subtotal = _last_amount(_after_label(others, SUBTOTAL_LABELS, signed=True))
        total = _last_amount(_after_label(others, TOTAL_LABELS, skip=SUBTOTAL_LINE, signed=True))
        invoice = Invoice(
            vendor_name=_vendor(lines),
            customer_name=_after_label(others, CUSTOMER_LABELS),
            invoice_number=_invoice_number(others),
            invoice_date=invoice_date,
            due_date=parse_date(_after_label(others, DUE_DATE_LABELS) or ""),
            currency=_currency(text),
            subtotal=subtotal,
            tax=_tax(_after_label(others, TAX_LABELS, skip=NOT_A_TAX_AMOUNT, signed=True), subtotal, total),
            total=total,
            line_items=line_items,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        return ExtractionResult(invoice=invoice, extractor=self.name, latency_ms=latency_ms)
