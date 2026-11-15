"""Adversarial unit coverage for QuoteHawk's non-negotiable cost guardrails."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.models import (
    FxRate,
    NormalizedQuote,
    PaymentMilestone,
    PaymentTerms,
    QuoteLine,
    QuoteSource,
    persisted_quote_from_payload,
)
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


def test_landed_cost_includes_shipping_as_a_separate_required_fee(requisition) -> None:
    quote = make_quote(freight_cost="100.00", shipping_cost="35.00", duties_cost="50.00")

    result = calculate_landed_cost(requisition, quote, PricingPolicy())

    assert result.freight_cost == Decimal("100.00")
    assert result.shipping_cost == Decimal("35.00")
    assert result.duties_cost == Decimal("50.00")
    assert result.total_landed_cost == Decimal("1185.00")


def test_over_budget_bid_is_visible_but_not_approval_ready(requisition) -> None:
    quote = make_quote(quote_id="Q-OVER", freight_cost="500.00")

    comparison = compare_quotes(requisition, [quote], PricingPolicy())
    score = comparison.ranked_quotes[0]

    assert score.approval_ready is False
    assert "exceeds requisition budget" in score.guardrail_messages[0]


def test_currency_mismatch_requires_an_explicit_fx_rate(requisition) -> None:
    quote = make_quote(currency="EUR")

    with pytest.raises(PolicyViolation, match="explicit FX rate"):
        calculate_landed_cost(requisition, quote, PricingPolicy())


def test_explicit_fx_rate_converts_each_landed_cost_component_to_the_pr_currency(
    requisition,
) -> None:
    quote = make_quote(currency="EUR")
    rate = FxRate(source_currency="EUR", target_currency="USD", rate=Decimal("1.10"))

    result = calculate_landed_cost(requisition, quote, PricingPolicy(), fx_rates=(rate,))

    assert result.currency == "USD"
    assert result.source_currency == "EUR"
    assert result.fx_rate == Decimal("1.10")
    assert result.line_total == Decimal("1100.00")
    assert result.freight_cost == Decimal("110.00")
    assert result.duties_cost == Decimal("55.00")
    assert result.total_landed_cost == Decimal("1265.00")


def test_milestone_and_letter_of_credit_terms_have_deterministic_effective_days(
    requisition,
) -> None:
    milestone_payload = make_quote().model_dump(mode="python")
    milestone_payload.update(
        {
            "payment_terms": PaymentTerms.MILESTONE,
            "payment_milestones": (
                PaymentMilestone(percentage=Decimal("0.40"), due_days=0),
                PaymentMilestone(percentage=Decimal("0.60"), due_days=60),
            ),
        }
    )
    milestone_quote = NormalizedQuote.model_validate(milestone_payload)
    letter_of_credit_payload = make_quote(quote_id="Q-LC", vendor_id="vendor-lc").model_dump(
        mode="python"
    )
    letter_of_credit_payload.update(
        {
            "payment_terms": PaymentTerms.LETTER_OF_CREDIT,
            "payment_term_days": 75,
        }
    )
    letter_of_credit_quote = NormalizedQuote.model_validate(letter_of_credit_payload)

    assert milestone_quote.effective_payment_days == 36
    assert letter_of_credit_quote.effective_payment_days == 75
    comparison = compare_quotes(
        requisition,
        [milestone_quote, letter_of_credit_quote],
        PricingPolicy(target_payment_days=90, daily_financing_rate=Decimal("0.001")),
    )
    assert comparison.ranked_quotes[0].quote_id == "Q-LC"


def test_configurable_comparison_capacity_supports_twenty_supplier_bids(requisition) -> None:
    quotes = [
        make_quote(
            quote_id=f"Q-{index:02d}",
            vendor_id=f"vendor-{index:02d}",
            unit_price=f"{Decimal('10') + Decimal(index) / Decimal('100'):.2f}",
        )
        for index in range(20)
    ]

    comparison = compare_quotes(requisition, quotes, PricingPolicy())

    assert len(comparison.ranked_quotes) == 20
    with pytest.raises(PolicyViolation, match="one and 20"):
        compare_quotes(
            requisition,
            [*quotes, make_quote(quote_id="Q-20", vendor_id="vendor-20")],
            PricingPolicy(),
        )


def test_missing_freight_is_a_schema_failure_not_a_free_fee() -> None:
    payload = {
        "quote_id": "Q-MISSING-FREIGHT",
        "requisition_id": "PR-1001",
        "vendor_id": "vendor-risky",
        "vendor_name": "Risky Components",
        "source": QuoteSource.EMAIL,
        "currency": "USD",
        "lines": [{"sku": "FILTER-10", "quoted_quantity": "100", "unit_price": "10.00"}],
        "shipping_cost": "0.00",
        "duties_cost": "0.00",
        "lead_time_days": 7,
        "payment_terms": PaymentTerms.NET_30,
    }

    with pytest.raises(ValidationError, match="freight_cost"):
        NormalizedQuote.model_validate(payload)


def test_missing_shipping_is_a_schema_failure_not_a_free_fee() -> None:
    payload = make_quote().model_dump(mode="json")
    del payload["shipping_cost"]

    with pytest.raises(ValidationError, match="shipping_cost"):
        NormalizedQuote.model_validate(payload)


def test_legacy_persisted_quote_can_resume_with_zero_separate_shipping() -> None:
    legacy_payload = make_quote().model_dump(mode="json")
    del legacy_payload["shipping_cost"]

    quote = persisted_quote_from_payload(legacy_payload)

    assert quote.shipping_cost == Decimal("0")


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
