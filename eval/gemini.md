# Evaluation: `gemini` (gemini-3.5-flash-lite)

20 labelled invoices across 5 layouts.

| Measure | Result |
|---|---|
| Fields correct | 100.0% |
| Documents with every field correct | 20 of 20 |
| Documents wrong on a critical field | 0 of 20 |
| ...of which validation sent to review | 0 |
| ...of which slipped through unnoticed | 0 |
| Documents the extractor could not read | 0 of 20 |
| Tokens (input / output) | 13,880 / 5,794 |
| Cost at list price | $0.018649 |
| Time | 219.0 s |

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
| eu | 4 of 4 |
| freelance | 4 of 4 |
| pk | 4 of 4 |
| uk | 4 of 4 |
| us | 4 of 4 |
