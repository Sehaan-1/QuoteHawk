"""Reusable synthetic procurement fixtures for credential-free tests."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.models import (
    NormalizedQuote,
    PaymentTerms,
    PurchaseRequisition,
    QuoteLine,
    QuoteSource,
    RequisitionLine,
)


@pytest.fixture
def requisition() -> PurchaseRequisition:
    return PurchaseRequisition(
        requisition_id="PR-1001",
        currency="USD",
        items=(
            RequisitionLine(
                sku="FILTER-10",
                description="Industrial filter cartridge",
                quantity=Decimal("100"),
            ),
        ),
        max_landed_cost=Decimal("1400.00"),
        target_lead_time_days=7,
    )


def make_quote(
    *,
    quote_id: str = "Q-ALPHA-1",
    vendor_id: str = "vendor-alpha",
    vendor_name: str = "Alpha Industrial Supply",
    unit_price: str = "10.00",
    freight_cost: str = "100.00",
    duties_cost: str = "50.00",
    lead_time_days: int = 7,
    payment_terms: PaymentTerms = PaymentTerms.NET_30,
    minimum_order_quantity: str = "0",
    quality_penalty: str = "0",
    early_payment_discount_rate: str = "0",
    currency: str = "USD",
) -> NormalizedQuote:
    return NormalizedQuote(
        quote_id=quote_id,
        requisition_id="PR-1001",
        vendor_id=vendor_id,
        vendor_name=vendor_name,
        source=QuoteSource.SIMULATOR,
        currency=currency,
        lines=(
            QuoteLine(
                sku="FILTER-10",
                quoted_quantity=Decimal("100"),
                unit_price=Decimal(unit_price),
            ),
        ),
        freight_cost=Decimal(freight_cost),
        duties_cost=Decimal(duties_cost),
        lead_time_days=lead_time_days,
        payment_terms=payment_terms,
        minimum_order_quantity=Decimal(minimum_order_quantity),
        quality_penalty=Decimal(quality_penalty),
        early_payment_discount_rate=Decimal(early_payment_discount_rate),
    )
