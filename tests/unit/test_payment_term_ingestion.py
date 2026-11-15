"""Structured payment terms remain strict across supplier-document ingestion."""

from __future__ import annotations

from app.domain.models import PaymentTerms, QuoteSource
from app.ingestion.documents import SupplierQuoteDocument
from app.ingestion.extractors import ExtractionProvider, extract_supplier_documents


def _email(payment_terms: str, extra: str = "") -> SupplierQuoteDocument:
    return SupplierQuoteDocument(
        source=QuoteSource.EMAIL,
        content=f"""Quote ID: PAYMENT-1
Requisition ID: PR-1001
Vendor ID: payment-supplier
Vendor Name: Payment Supply
Currency: USD
Freight Cost: USD 100.00
Shipping Cost: USD 35.00
Duties Cost: USD 50.00
Lead Time Days: 7 days
Payment Terms: {payment_terms}
{extra}Line Item: FILTER-10 | 100 | USD 10.00
""",
    )


def test_rule_based_email_normalizes_milestone_billing() -> None:
    quote = extract_supplier_documents(
        (_email("Milestone", "Payment Milestones: 40% at 0 days; 60% at 60 days\n"),),
        provider=ExtractionProvider.RULE_BASED,
    )[0]

    assert quote.payment_terms is PaymentTerms.MILESTONE
    assert quote.effective_payment_days == 36


def test_rule_based_email_normalizes_letter_of_credit_timing() -> None:
    quote = extract_supplier_documents(
        (_email("Letter of Credit", "Letter of Credit Days: 75\n"),),
        provider=ExtractionProvider.RULE_BASED,
    )[0]

    assert quote.payment_terms is PaymentTerms.LETTER_OF_CREDIT
    assert quote.effective_payment_days == 75
