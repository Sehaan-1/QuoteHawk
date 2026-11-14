"""Adversarial unit coverage for QuoteHawk's non-negotiable cost guardrails."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.models import NormalizedQuote, PaymentTerms, QuoteLine, QuoteSource
from app.policies.pricing import (
    PolicyViolation,
    PricingPolicy,
    calculate_landed_cost,
    compare_quotes,
)
from tests.conftest import make_quote


def test_landed_cost_includes_hidden_commercial_costs(requisition) -> None:
    """MOQ, payment, and lead-time costs cannot disappear from a low unit price."""

    quote = make_quote(
        lead_time_days=10,
        minimum_order_quantity="120",
        payment_terms=PaymentTerms.NET_30,
    )
    policy = PricingPolicy(
        included_lead_time_days=7,
        lead_time_penalty_per_day=Decimal("5"),
        target_payment_days=60,
        daily_financing_rate=Decimal("0.0005"),
    )

    result = calculate_landed_cost(requisition, quote, policy)

    assert result.line_total == Decimal("1000.00")
    assert result.lead_time_penalty == Decimal("15.00")
    assert result.minimum_order_penalty == Decimal("200.00")
    assert result.payment_term_penalty == Decimal("15.00")
    assert result.total_landed_cost == Decimal("1380.00")


def test_over_budget_bid_is_visible_but_not_approval_ready(requisition) -> None:
    quote = make_quote(quote_id="Q-OVER", freight_cost="500.00")

    comparison = compare_quotes(requisition, [quote], PricingPolicy())
    score = comparison.ranked_quotes[0]

    assert score.approval_ready is False
    assert "exceeds requisition budget" in score.guardrail_messages[0]


def test_currency_mismatch_fails_instead_of_guessing_an_exchange_rate(requisition) -> None:
    quote = make_quote(currency="EUR")

    with pytest.raises(PolicyViolation, match="currency conversion is not enabled"):
        calculate_landed_cost(requisition, quote, PricingPolicy())


def test_missing_freight_is_a_schema_failure_not_a_free_fee() -> None:
    payload = {
        "quote_id": "Q-MISSING-FREIGHT",
        "requisition_id": "PR-1001",
        "vendor_id": "vendor-risky",
        "vendor_name": "Risky Components",
        "source": QuoteSource.EMAIL,
        "currency": "USD",
        "lines": [{"sku": "FILTER-10", "quoted_quantity": "100", "unit_price": "10.00"}],
        "duties_cost": "0.00",
        "lead_time_days": 7,
        "payment_terms": PaymentTerms.NET_30,
    }

    with pytest.raises(ValidationError, match="freight_cost"):
        NormalizedQuote.model_validate(payload)


def test_negative_supplier_fee_is_rejected_before_policy_runs() -> None:
    with pytest.raises(ValidationError, match="freight_cost"):
        make_quote(freight_cost="-0.01")


def test_quote_with_insufficient_quantity_cannot_be_scored(requisition) -> None:
    quote = make_quote()
    smaller_line = QuoteLine(
        sku="FILTER-10", quoted_quantity=Decimal("99"), unit_price=Decimal("10")
    )
    insufficient_quote = quote.model_copy(update={"lines": (smaller_line,)})

    with pytest.raises(PolicyViolation, match="below the requested quantity"):
        calculate_landed_cost(requisition, insufficient_quote, PricingPolicy())
