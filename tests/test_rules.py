from datetime import date
from decimal import Decimal

import pytest

from doc2data.extractors.rules import RulesExtractor, _last_amount, parse_amount, parse_date
from doc2data.pdf_text import extract_text
from doc2data.schema import Invoice


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("$1,234.56", "1234.56"),
        ("1.234,56 EUR", "1234.56"),
        ("190,00", "190.00"),
        ("12,500.00 PKR", "12500.00"),
        ("12.5", "12.5"),
        ("7", "7"),
        ("1.234", "1234"),
        ("1,234,567", "1234567"),
        ("1 190,00", "1190.00"),
        ("0.125", "0.125"),
        ("0,125", "0.125"),
        ("1234.567", "1234.567"),
    ],
)
def test_parse_amount_handles_both_decimal_conventions(raw, expected):
    assert parse_amount(raw) == Decimal(expected)


def test_parse_amount_rejects_text_without_digits():
    assert parse_amount("applies.") is None


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2026-11-03", date(2026, 11, 3)),
        ("3 November 2026.", date(2026, 11, 3)),
        ("03-Nov-2026", date(2026, 11, 3)),
        ("November 3, 2026", date(2026, 11, 3)),
        ("11/03/2026", date(2026, 11, 3)),  # slashes are read month-first
        ("03.11.2026", date(2026, 11, 3)),  # dots are read day-first
        ("17/11/2026", date(2026, 11, 17)),  # impossible month, so the parts are swapped
        ("31/02/2026", None),
        ("no date here", None),
    ],
)
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("$450.00", "450.00"),
        ("-$450.00", "-450.00"),  # a minus touching the value is a sign
        ("$-450.00", "-450.00"),
        ("($450.00)", "-450.00"),  # accounting brackets
        ("(8.25%) $42.90", "42.90"),  # the percentage is not the amount
        ("1 190,00 EUR", "1190.00"),  # thousands grouped by a space
        ("AED 7,521.15", "7521.15"),
        ("20 784.00", "784.00"),  # a rate and an amount, not twenty thousand
        ("12 600.00", "600.00"),  # with a decimal point, spaces are never joined
        ("20% on items 1-3 784.00", "784.00"),
        ("562.90 USD Tel 555-0199", "199"),  # a hyphen inside a number is not a minus
    ],
)
def test_last_amount_keeps_the_sign_and_skips_percentages(raw, expected):
    assert _last_amount(raw) == Decimal(expected)


def test_a_line_with_only_a_percentage_has_no_amount():
    assert _last_amount("20%") is None


def read(text: str) -> Invoice:
    return RulesExtractor().extract(text=text).invoice


def test_space_grouped_thousands_are_read_whole():
    invoice = read("Net amount 1 000,00 EUR\nVAT 19% 190,00 EUR\nTotal amount 1 190,00 EUR")
    assert (invoice.subtotal, invoice.tax, invoice.total) == (Decimal("1000.00"), Decimal("190.00"), Decimal("1190.00"))


def test_a_credit_note_keeps_its_minus_and_is_sent_to_review():
    from doc2data.validation import validate

    invoice = read("Acme Co\nInvoice #: C-1\nDate: 2026-10-01\nTotal Due -$450.00")
    assert invoice.total == Decimal("-450.00")
    assert "non_positive_total" in {issue.code for issue in validate(invoice)}


@pytest.mark.parametrize("line", ["Total - $450.00", "Total-562.90 USD", "Total: - 450.00", "Total ....-$450.00"])
def test_a_hyphen_used_as_a_separator_is_not_a_minus(line):
    assert read(line).total > 0


@pytest.mark.parametrize("line", ["Invoice No-1041", "Invoice #-1041", "Invoice No: 1041", "Invoice number - 1041"])
def test_a_hyphen_after_the_invoice_number_label_is_a_separator(line):
    assert read(line).invoice_number == "1041"


def test_descriptions_with_colons_are_still_line_items():
    invoice = read(
        "Consulting: October 10 100.00 1000.00\n"
        "Hosting (period: Q3) 1 50.00 50.00\n"
        "Support 09:00-17:00 2 25.00 50.00\n"
        "Subtotal 1100.00"
    )
    assert [item.amount for item in invoice.line_items] == [Decimal("1000.00"), Decimal("50.00"), Decimal("50.00")]
    assert invoice.subtotal == Decimal("1100.00")


@pytest.mark.parametrize(
    "line",
    [
        "Tax return preparation 1 450.00 450.00",
        "VAT return filing 1 300.00 300.00",
        "Due diligence report 1 5000.00 5000.00",
        "Total station rental 2 50.00 100.00",
        "Date palm seedlings 10 5.00 50.00",
        "GST registration service 1 99.00 99.00",
        "Issued licences 3 10.00 30.00",
    ],
)
def test_an_item_whose_description_starts_with_a_label_word_is_kept(line):
    assert len(read(line).line_items) == 1


@pytest.mark.parametrize(
    "line",
    ["Subtotal 2 100.00 200.00", "Total AED 2 100.00 200.00", "Invoice No: 12 345 678", "VAT Reg No: GB 123 4567 89",
     "Sales Tax: 1 20.00 20.00", "Due Date 3 11 2026"],
)
def test_a_label_followed_by_its_value_is_not_an_item(line):
    assert read(line).line_items == []


def test_a_tax_rate_written_without_a_percent_sign_is_not_joined_to_the_amount():
    # "20 784,00" could be twenty thousand. The other figures on the invoice decide.
    assert read("VAT 20 784,00\nTotal 4 704,00 EUR").tax == Decimal("784.00")
    assert read("Net amount 3 920,00\nVAT 20 784,00\nTotal amount 4 704,00").tax == Decimal("784.00")
    assert read("VAT 19 114,00\nTotal 714,00").tax == Decimal("114.00")
    large = read("Net amount 109 389,47\nVAT 20 784,00\nTotal amount 130 173,47")
    assert large.tax == Decimal("20784.00")  # here the joined reading is the one that adds up


@pytest.mark.parametrize(
    "line",
    [
        "Total amount due 12 345 678,90",
        "Total excl. VAT 1 234 567,00 EUR",
        "Subtotal excl. VAT 1 234 567,00",
        "VAT amount 1 234 567,00",
        "Date of supply 3 11 2026",
        "Tax point date 3 11 2026",
        "Due date Friday 4 12 2026",
        "Total usd 2 100.00 200.00",
        "Tel: 020 7946 0958",
        "IBAN: DE89 3704 0044 0532 0130 00",
    ],
)
def test_lines_that_only_look_like_items_are_not_items(line):
    assert read(line).line_items == []


def test_an_item_must_have_an_items_arithmetic():
    # 2 x 10.00 is not 25.00. The rules cannot tell a discounted item from a misread line,
    # so they leave it out. Another item was read here, so the line-total check sends this
    # invoice to review. Had the widget been the only item, the invoice would have been
    # stored with no line items and its totals checked on their own.
    invoice = read("Widget 2 10.00 25.00\nGadget 3 5.00 15.00\nSubtotal 40.00\nTotal 40.00")
    assert [item.description for item in invoice.line_items] == ["Gadget"]


def test_without_a_subtotal_a_tax_is_split_only_when_the_rate_checks_out():
    # Part-paid invoices: the figure labelled as due is smaller than the tax.
    assert read("VAT 1 200,00\nAmount due 500,00").tax == Decimal("1200.00")
    assert read("VAT 1 200,00\nBalance due 200,00").tax == Decimal("1200.00")
    assert read("VAT 12 345 678,90\nTotal 100,00").tax == Decimal("12345678.90")


def test_totals_lines_are_never_line_items():
    invoice = read("Widget 2 10,00 20,00\nNet amount 12 345 678,90 EUR\nTotal amount 14 691 357,89 EUR")
    assert [item.description for item in invoice.line_items] == ["Widget"]
    assert invoice.subtotal == Decimal("12345678.90")


def extract(samples_dir, file) -> Invoice:
    text, _ = extract_text((samples_dir / file).read_bytes())
    return RulesExtractor().extract(text=text).invoice


def test_rules_read_the_us_layout_completely(samples_dir, truth):
    expected = Invoice.model_validate(truth["us-01.pdf"]["invoice"])
    assert extract(samples_dir, "us-01.pdf") == expected


def test_vat_registration_number_is_not_mistaken_for_the_tax_amount(samples_dir, truth):
    invoice = extract(samples_dir, "uk-01.pdf")
    assert invoice.tax == Decimal(truth["uk-01.pdf"]["invoice"]["tax"])


def test_labelled_lines_are_not_read_as_line_items(samples_dir, truth):
    # "VAT Reg No: GB 123 4567 89" has the shape of description, quantity, price, amount.
    invoice = extract(samples_dir, "uk-01.pdf")
    assert len(invoice.line_items) == len(truth["uk-01.pdf"]["invoice"]["line_items"])
    assert all(":" not in item.description for item in invoice.line_items)


def test_rules_swap_day_and_month_on_an_ambiguous_uk_date(samples_dir, truth):
    # Another known limit: 08/09/2026 (8 September) is read month-first. No arithmetic check reveals it.
    invoice = extract(samples_dir, "uk-02.pdf")
    assert invoice.invoice_date.isoformat() != truth["uk-02.pdf"]["invoice"]["invoice_date"]


def test_rules_miss_a_vendor_that_only_appears_in_the_footer(samples_dir, truth):
    # A known limit of the baseline, pinned here so a change in behaviour is noticed.
    invoice = extract(samples_dir, "eu-01.pdf")
    assert invoice.vendor_name != truth["eu-01.pdf"]["invoice"]["vendor_name"]
    assert invoice.total == Decimal(truth["eu-01.pdf"]["invoice"]["total"])


def test_a_single_digit_rate_is_split_from_the_amount_too():
    # 5 400,00 is below the total, so only the rate check can tell it is 5% and 400,00.
    assert read("VAT 5 400,00\nTotal 8 400,00 EUR").tax == Decimal("400.00")
    assert read("VAT 7 700,00\nTotal 10 700,00 EUR").tax == Decimal("700.00")
    assert read("VAT 20 784,00\nTotal 124 704,00 EUR").tax == Decimal("20784.00")  # really twenty thousand


def test_with_a_subtotal_the_tax_is_the_reading_that_makes_the_total():
    # 794,00 is not 20% of the net, so the rate cannot settle it; total minus subtotal does.
    assert read("Subtotal 3 920,00\nVAT 20 794,00\nTotal 4 714,00").tax == Decimal("794.00")
    # Here the joined figure is the one that adds up.
    assert read("Subtotal 103 920,00\nVAT 20 784,00\nTotal 124 704,00").tax == Decimal("20784.00")
    # Neither reading adds up, so the figure stays as printed and the totals check flags the invoice.
    assert read("Subtotal 3 920,00\nShipping 50,00\nVAT 20 794,00\nTotal 4 764,00").tax == Decimal("20794.00")


def test_prices_grouped_with_a_space_are_read_whole():
    invoice = read(
        "Laptop 1 1 299,00 1 299,00\nNet amount 1 299,00 EUR\nVAT 19% 246,81 EUR\nTotal amount 1 545,81 EUR"
    )
    (item,) = invoice.line_items
    assert (item.description, item.quantity, item.unit_price, item.amount) == (
        "Laptop", Decimal("1"), Decimal("1299.00"), Decimal("1299.00"),
    )
    hours = read("Consulting 2 1 000,00 2 000,00\nWorkshop day 2 1 450,00 2 900,00").line_items
    assert [(item.description, item.unit_price, item.amount) for item in hours] == [
        ("Consulting", Decimal("1000.00"), Decimal("2000.00")),
        ("Workshop day", Decimal("1450.00"), Decimal("2900.00")),
    ]


def test_item_arithmetic_allows_a_cent_of_rounding_and_no_more():
    assert len(read("Paper 3 33.33 100.00").line_items) == 1  # 99.99 printed as 100.00
    assert read("Paper 3 33.33 100.05").line_items == []


@pytest.mark.parametrize(
    "line",
    [
        "Conference registration non-member 1 450.00 450.00",
        "Vehicle registration notice fee 1 25.00 25.00",
        "Tax ID application 1 80.00 80.00",
    ],
)
def test_words_that_also_appear_on_tax_registration_lines_do_not_hide_an_item(line):
    assert len(read(line).line_items) == 1


def test_a_line_read_as_an_item_is_not_also_read_as_a_label():
    invoice = read("Tax return preparation 1 450.00 450.00\nSubtotal 450.00\nTax 37.13\nTotal USD 487.13")
    assert (invoice.tax, invoice.total) == (Decimal("37.13"), Decimal("487.13"))

    invoice = read(
        "Site survey 2 100.00 200.00\nTotal station rental 2 50.00 100.00\n"
        "Subtotal 300.00\nTax 30.00\nTotal USD 330.00"
    )
    assert invoice.total == Decimal("330.00") and len(invoice.line_items) == 2

    invoice = read("Due diligence report 1 5000.00 5000.00\nSubtotal 5000.00\nTotal USD 5000.00\nDue: 2026-10-31")
    assert invoice.due_date == date(2026, 10, 31)

    invoice = read("Customer support 2 50.00 100.00\nBilled by: Acme\nCustomer: Fennel & Rye Foods Ltd\nTotal 100.00")
    assert invoice.customer_name == "Fennel & Rye Foods Ltd"


@pytest.mark.parametrize(
    "line, expected",
    [
        ("Phase 2 1 500,00 500,00", ("Phase 2", "1", "500.00", "500.00")),
        ("Wartung Oktober 2026 1 450,00 450,00", ("Wartung Oktober 2026", "1", "450.00", "450.00")),
        ("Lizenz Office 365 2 150,00 300,00", ("Lizenz Office 365", "2", "150.00", "300.00")),
        ("Monitor 27 2 349,00 698,00", ("Monitor 27", "2", "349.00", "698.00")),
        ("Support Q3 2026 3 120,00 360,00", ("Support Q3 2026", "3", "120.00", "360.00")),
        ("Paket 3 2 100,00 200,00", ("Paket 3", "2", "100.00", "200.00")),
        ("Server 2 1 200,00 2 400,00", ("Server", "2", "1200.00", "2400.00")),
    ],
)
def test_a_description_that_ends_in_a_number_is_split_where_the_arithmetic_holds(line, expected):
    (item,) = read(line).line_items
    assert (item.description, item.quantity, item.unit_price, item.amount) == (
        expected[0], *(Decimal(value) for value in expected[1:]),
    )


def test_item_lines_are_not_searched_for_the_header_and_totals():
    invoice = read(
        "Invoice number audit 1 120.00 120.00\n"
        "Issued licences 3 10.00 30.00\n"
        "Date palm seedlings 10 5.00 50.00\n"
        "Net amount adjustment 1 10.00 10.00\n"
        "Invoice number: INV-7\nIssued: 2026-10-01\nNet amount 210.00\nTotal 210.00"
    )
    assert (invoice.invoice_number, invoice.invoice_date) == ("INV-7", date(2026, 10, 1))
    assert (invoice.subtotal, len(invoice.line_items)) == (Decimal("210.00"), 4)

    dated = read("Date palm seedlings 10 5.00 50.00\nDate: 2026-10-01\nTotal 50.00")
    assert dated.invoice_date == date(2026, 10, 1)
