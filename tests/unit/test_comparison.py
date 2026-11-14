"""Comparison-matrix behavior without an LLM or network connection."""

from __future__ import annotations

from decimal import Decimal

from app.policies.pricing import PricingPolicy, compare_quotes
from tests.conftest import make_quote


def test_weighted_comparison_ranks_eligible_bids_before_exceptions(requisition) -> None:
    expensive = make_quote(
        quote_id="Q-EXPENSIVE",
        vendor_id="vendor-expensive",
        unit_price="13.00",
        lead_time_days=1,
    )
    best_value = make_quote(
        quote_id="Q-BEST",
        vendor_id="vendor-best",
        unit_price="9.50",
        freight_cost="80.00",
        lead_time_days=5,
    )
    slower = make_quote(
        quote_id="Q-SLOW",
        vendor_id="vendor-slow",
        unit_price="9.60",
        freight_cost="80.00",
        lead_time_days=20,
    )

    comparison = compare_quotes(
        requisition,
        [expensive, slower, best_value],
        PricingPolicy(),
        supplier_performance={
            "vendor-expensive": Decimal("1"),
            "vendor-best": Decimal("0.75"),
            "vendor-slow": Decimal("0.75"),
        },
    )

    assert [score.quote_id for score in comparison.ranked_quotes] == [
        "Q-BEST",
        "Q-SLOW",
        "Q-EXPENSIVE",
    ]
    assert comparison.ranked_quotes[-1].approval_ready is False


def test_early_payment_discount_is_explicit_and_auditable(requisition) -> None:
    discounted = make_quote(
        early_payment_discount_rate="0.02",
        lead_time_days=0,
        freight_cost="0",
        duties_cost="0",
    )

    score = compare_quotes(requisition, [discounted], PricingPolicy()).ranked_quotes[0]

    assert score.landed_cost.early_payment_discount == Decimal("20.00")
    assert score.landed_cost.total_landed_cost == Decimal("980.00")
