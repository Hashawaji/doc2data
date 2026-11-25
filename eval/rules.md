# Evaluation: `rules`

20 labelled invoices across 5 layouts.

| Measure | Result |
|---|---|
| Fields correct | 86.0% |
| Documents with every field correct | 10 of 20 |
| Documents wrong on a critical field | 6 of 20 |
| ...of which validation sent to review | 1 |
| ...of which slipped through unnoticed | 5 |
| Documents the extractor could not read | 0 of 20 |
| Tokens (input / output) | 0 / 0 |
| Cost at list price | $0 |
| Time | 0.0 s |

Critical fields: vendor name, invoice number, invoice date, currency, total.

| Field | Correct |
|---|---|
| vendor name | 80% |
| customer name | 60% |
| invoice number | 100% |
| invoice date | 90% |
| due date | 90% |
| currency | 100% |
| subtotal | 100% |
| tax | 80% |
| total | 100% |
| line items | 60% |

| Layout | Every field correct |
|---|---|
| eu | 0 of 4 |
| freelance | 0 of 4 |
| pk | 4 of 4 |
| uk | 2 of 4 |
| us | 4 of 4 |

Slipped through: `eu-01.pdf`, `eu-02.pdf`, `eu-03.pdf`, `eu-04.pdf`, `uk-02.pdf`.
