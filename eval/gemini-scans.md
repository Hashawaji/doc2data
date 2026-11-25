# Evaluation: `gemini` (gemini-3.5-flash-lite)

5 labelled invoices across 5 layouts.

| Measure | Result |
|---|---|
| Fields correct | 100.0% |
| Documents with every field correct | 5 of 5 |
| Documents wrong on a critical field | 0 of 5 |
| ...of which validation sent to review | 0 |
| ...of which slipped through unnoticed | 0 |
| Documents the extractor could not read | 0 of 5 |
| Tokens (input / output) | 3,470 / 1,488 |
| Cost at list price | $0.004761 |
| Time | 28.0 s |

Critical fields: vendor name, invoice number, invoice date, currency, total.

| Field | Correct |
|---|---|
| vendor name | 100% |
| customer name | 100% |
| invoice number | 100% |
| invoice date | 100% |
| due date | 100% |
| currency | 100% |
| subtotal | 100% |
| tax | 100% |
| total | 100% |
| line items | 100% |

| Layout | Every field correct |
|---|---|
| eu | 1 of 1 |
| freelance | 1 of 1 |
| pk | 1 of 1 |
| uk | 1 of 1 |
| us | 1 of 1 |
