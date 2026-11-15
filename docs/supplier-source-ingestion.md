# Supplier source-ingestion contract

QuoteHawk accepts **three to five** supplier quote artifacts for one purchase
requisition through `POST /api/rfq/source-runs`. It normalizes each artifact into
a strict `NormalizedQuote`, then runs the same tenant-scoped, draft-only RFQ
workflow used for already-normalized fixture submissions.

This is an ingestion boundary, not a vendor mailbox or an invoice archive:

- source documents are processed in request memory only;
- raw email text, CSV bodies, and PDF bytes are not written to the RFQ snapshot
  or QuoteHawk's structured logs;
- an `Idempotency-Key`, when supplied, hashes the exact request in memory; only
  the resulting one-way fingerprint is persisted;
- QuoteHawk does not send a supplier message, write to an ERP, or create a
  binding purchase order.

## Request shape

```json
{
  "requisition": { "...": "the existing PurchaseRequisition contract" },
  "supplier_documents": [
    { "source": "csv", "content": "...", "content_encoding": "plain_text" },
    { "source": "email", "content": "...", "content_encoding": "plain_text" },
    {
      "source": "pdf",
      "content": "base64-encoded PDF bytes",
      "content_encoding": "base64",
      "filename": "supplier-quote.pdf"
    }
  ],
  "provider": "rule_based",
  "observed_on": "2026-11-14",
  "run_id": "optional-client-run-id"
}
```

`source` may be `csv`, `email`, `pdf`, or `pdf_text`. `pdf` is base64 encoded;
all other source types must use plain text. The source count is deliberately
bounded to three through five documents, and each document must normalize to one
unique supplier/vendor bid.

`rule_based` is the credential-free default. `gemini` is an explicit opt-in
provider when `GOOGLE_API_KEY` or `GEMINI_API_KEY` is configured. Both providers
return through the exact same strict Pydantic schema and deterministic policy
layer. `fixture` remains restricted to the legacy normalized-JSON extraction
endpoint and is not a source-document provider.

## Required commercial values

Every source must make these facts explicit. Zero is accepted for a fee only
when it is written as zero; a missing value is rejected.

- quote ID, requisition ID, stable vendor ID, vendor name, and currency;
- one or more SKU, quoted-quantity, and unit-price lines;
- freight, **shipping**, and duties as separate amounts;
- lead time, MOQ, and supported payment terms (`Net 30` or `Net 60`, with the
  existing `Net 10`/`Net 45` contract also accepted).

Optional quality penalty, early-payment discount rate, and validity date retain
the existing normalized-quote contract. Landed cost deterministically includes
unit-price line totals, freight, shipping, duties, lead-time penalties, MOQ
penalties, and any configured payment-term financing penalty. It uses `Decimal`
throughout and never delegates calculations or ranking to Gemini.

## Rule-based CSV format

CSV is strict so unsupported columns cannot smuggle untrusted instructions into
the normalization boundary. Use these required headers on every line:

```csv
quote_id,requisition_id,vendor_id,vendor_name,currency,freight_cost,shipping_cost,duties_cost,lead_time_days,payment_terms,minimum_order_quantity,sku,quoted_quantity,unit_price
CSV-QUOTE-1,PR-1001,vendor-csv,CSV Supply,USD,100.00,35.00,50.00,7,net_30,100,FILTER-10,100,10.00
```

Optional supported headers are `quality_penalty`, `early_payment_discount_rate`,
and `valid_until`. Repeat scalar quote values consistently on every line. Column
names may use spaces or hyphens in place of underscores; unknown, duplicate, or
conflicting columns/values are rejected.

## Rule-based email and text-PDF format

Plain supplier email text and text extracted from a PDF use labels in any order,
plus one or more `Line Item` rows. Narrative text is not executed and cannot
change QuoteHawk policy.

```text
Quote ID: EMAIL-QUOTE-1
Requisition ID: PR-1001
Vendor ID: vendor-email
Vendor Name: Email Supply
Currency: USD
Freight Cost: USD 100.00
Shipping Cost: USD 35.00
Duties Cost: USD 50.00
Lead Time Days: 7 days
Payment Terms: Net 60
MOQ: 100
Line Item: FILTER-10 | 100 | USD 10.00
```

The line separator can be a vertical bar, comma, or semicolon. The order is SKU,
quoted quantity, unit price. Text-based PDFs use this exact parser after local
text extraction with `pypdf`; encrypted PDFs, oversized PDFs, and PDFs without
extractable text are rejected by `rule_based`. An explicitly configured Gemini
provider may receive a bounded PDF directly for model-assisted extraction, but
its result is still schema-validated and then scored by deterministic code.

## Supplier memory and auditability

Before it scores a new normalized bid, QuoteHawk loads the tenant-scoped supplier
memory snapshot from SQLite or the explicitly selected Postgres repository. That
snapshot supplies prior unit-price/price-creep and freight baselines, average
delivery delay, responsiveness, and historical discount margin. The new bid is
persisted **only after** analysis, so it cannot dilute its own anomaly baseline.
The durable RFQ snapshot retains the normalized commercial facts, comparison,
anomalies, and concise audit events—not the raw source artifact.
