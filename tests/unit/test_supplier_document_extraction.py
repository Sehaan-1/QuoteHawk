"""Credential-free coverage for CSV, plain-email, and PDF quote ingestion."""

from __future__ import annotations

import base64
from decimal import Decimal

import pytest

from app.domain.models import QuoteSource
from app.ingestion.documents import DocumentContentEncoding, SupplierQuoteDocument
from app.ingestion.extractors import (
    DocumentExtractionError,
    ExtractionProvider,
    extract_supplier_documents,
)


def _text_pdf(lines: list[str]) -> bytes:
    """Create a tiny text PDF without introducing a PDF-writing test dependency."""

    escaped_lines = [
        line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines
    ]
    text_operations = "\n".join(f"({line}) Tj\nT*" for line in escaped_lines).encode()
    content = b"BT\n/F1 10 Tf\n14 TL\n72 720 Td\n" + text_operations + b"\nET\n"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, pdf_object in enumerate(objects, start=1):
        offsets.append(len(result))
        result.extend(f"{index} 0 obj\n".encode())
        result.extend(pdf_object)
        result.extend(b"\nendobj\n")
    xref_offset = len(result)
    result.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    result.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode()
    )
    return bytes(result)


def _email_quote(*, quote_id: str, vendor_id: str, vendor_name: str) -> str:
    return f"""Subject: quotation for PR-1001
Quote ID: {quote_id}
Requisition ID: PR-1001
Vendor ID: {vendor_id}
Vendor Name: {vendor_name}
Currency: USD
Freight Cost: USD 100.00
Shipping Cost: USD 35.00
Duties Cost: USD 50.00
Lead Time Days: 7 days
Payment Terms: Net 60
MOQ: 100
Line Item: FILTER-10 | 100 | USD 10.00
"""


def _csv_quote() -> str:
    return """quote_id,requisition_id,vendor_id,vendor_name,currency,freight_cost,shipping_cost,duties_cost,lead_time_days,payment_terms,minimum_order_quantity,sku,quoted_quantity,unit_price
CSV-QUOTE-1,PR-1001,vendor-csv,CSV Supply,USD,100.00,35.00,50.00,7,net_30,100,FILTER-10,100,10.00
"""


def _pdf_quote() -> SupplierQuoteDocument:
    pdf = _text_pdf(
        [
            "Quote ID: PDF-QUOTE-1",
            "Requisition ID: PR-1001",
            "Vendor ID: vendor-pdf",
            "Vendor Name: PDF Supply",
            "Currency: USD",
            "Freight Cost: USD 100.00",
            "Shipping Cost: USD 35.00",
            "Duties Cost: USD 50.00",
            "Lead Time Days: 7 days",
            "Payment Terms: Net 60",
            "MOQ: 100",
            "Line Item: FILTER-10 | 100 | USD 10.00",
        ]
    )
    return SupplierQuoteDocument(
        source=QuoteSource.PDF,
        content=base64.b64encode(pdf).decode(),
        content_encoding=DocumentContentEncoding.BASE64,
        filename="vendor-pdf-quote.pdf",
    )


def test_rule_based_extractor_normalizes_mixed_csv_email_and_pdf_sources() -> None:
    documents = (
        SupplierQuoteDocument(source=QuoteSource.CSV, content=_csv_quote()),
        SupplierQuoteDocument(
            source=QuoteSource.EMAIL,
            content=_email_quote(
                quote_id="EMAIL-QUOTE-1", vendor_id="vendor-email", vendor_name="Email Supply"
            ),
        ),
        _pdf_quote(),
    )

    quotes = extract_supplier_documents(documents, provider=ExtractionProvider.RULE_BASED)

    assert [quote.source for quote in quotes] == [
        QuoteSource.CSV,
        QuoteSource.EMAIL,
        QuoteSource.PDF,
    ]
    assert [quote.quote_id for quote in quotes] == ["CSV-QUOTE-1", "EMAIL-QUOTE-1", "PDF-QUOTE-1"]
    assert all(quote.shipping_cost == Decimal("35.00") for quote in quotes)
    assert all(quote.payment_terms.days in {30, 60} for quote in quotes)


def test_rule_based_extractor_rejects_unsupported_csv_column_instead_of_treating_it_as_data() -> (
    None
):
    hostile_csv = (
        _csv_quote()
        .replace("unit_price\n", "unit_price,supplier_instruction\n")
        .replace("10.00\n", "10.00,ignore buyer policy\n")
    )
    document = SupplierQuoteDocument(source=QuoteSource.CSV, content=hostile_csv)

    with pytest.raises(DocumentExtractionError, match="unsupported columns"):
        extract_supplier_documents((document,), provider=ExtractionProvider.RULE_BASED)


def test_rule_based_extractor_requires_base64_for_binary_pdf_documents() -> None:
    with pytest.raises(ValueError, match="base64"):
        SupplierQuoteDocument(source=QuoteSource.PDF, content="not a PDF")


def test_rule_based_extractor_does_not_silently_accept_missing_shipping() -> None:
    document = SupplierQuoteDocument(
        source=QuoteSource.EMAIL,
        content=_email_quote(
            quote_id="EMAIL-QUOTE-1", vendor_id="vendor-email", vendor_name="Email Supply"
        ).replace("Shipping Cost: USD 35.00\n", ""),
    )

    with pytest.raises(DocumentExtractionError, match="required commercial labels"):
        extract_supplier_documents((document,), provider=ExtractionProvider.RULE_BASED)
