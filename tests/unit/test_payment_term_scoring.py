"""Payment-term scoring follows the buyer's stated preference, not bid duration."""

from __future__ import annotations

from decimal import Decimal

from app.domain.models import PaymentMilestone, PaymentTerms
from app.policies.pricing import PricingPolicy, ScoreWeights, compare_quotes
from tests.conftest import make_quote

_PAYMENT_FIT_ONLY = ScoreWeights(
    landed_cost=Decimal("0"),
    lead_time=Decimal("0"),
    payment_terms=Decimal("1"),
    supplier_performance=Decimal("0"),
)


def test_payment_terms_score_by_distance_from_the_buyer_target(requisition) -> None:
    quotes = [
        make_quote(
            quote_id="NET-10",
            vendor_id="vendor-net-10",
            payment_terms=PaymentTerms.NET_10,
        ),
        make_quote(
            quote_id="NET-30",
            vendor_id="vendor-net-30",
            payment_terms=PaymentTerms.NET_30,
        ),
        make_quote(
            quote_id="NET-60",
            vendor_id="vendor-net-60",
            payment_terms=PaymentTerms.NET_60,
        ),
    ]

    comparison = compare_quotes(
        requisition.model_copy(update={"target_payment_days": 30}),
        quotes,
        PricingPolicy(score_weights=_PAYMENT_FIT_ONLY),
    )
    score_by_quote = {score.quote_id: score for score in comparison.ranked_quotes}

    assert [score.quote_id for score in comparison.ranked_quotes] == ["NET-30", "NET-10", "NET-60"]
    assert score_by_quote["NET-30"].payment_term_score == Decimal("100.00")
    assert score_by_quote["NET-10"].payment_term_score == Decimal("33.33")
    assert score_by_quote["NET-60"].payment_term_score == Decimal("0.00")


def test_structured_effective_days_use_the_same_buyer_target(requisition) -> None:
    milestone_quote = make_quote(
        quote_id="MILESTONE-30",
        vendor_id="vendor-milestone",
        payment_terms=PaymentTerms.MILESTONE,
        payment_milestones=(
            PaymentMilestone(percentage=Decimal("0.50"), due_days=0),
            PaymentMilestone(percentage=Decimal("0.50"), due_days=60),
        ),
    )
    longer_quote = make_quote(
        quote_id="NET-60",
        vendor_id="vendor-net-60",
        payment_terms=PaymentTerms.NET_60,
    )

    comparison = compare_quotes(
        requisition.model_copy(update={"target_payment_days": 30}),
        [longer_quote, milestone_quote],
        PricingPolicy(score_weights=_PAYMENT_FIT_ONLY),
    )

    assert milestone_quote.effective_payment_days == 30
    assert [score.quote_id for score in comparison.ranked_quotes] == ["MILESTONE-30", "NET-60"]
    assert comparison.ranked_quotes[0].payment_term_score == Decimal("100.00")
    assert comparison.ranked_quotes[1].payment_term_score == Decimal("0.00")


def test_zero_day_buyer_preference_does_not_divide_by_zero(requisition) -> None:
    comparison = compare_quotes(
        requisition.model_copy(update={"target_payment_days": 0}),
        [
            make_quote(
                quote_id="NET-0",
                vendor_id="vendor-net-0",
                payment_terms=PaymentTerms.NET_0,
            ),
            make_quote(
                quote_id="NET-10",
                vendor_id="vendor-net-10",
                payment_terms=PaymentTerms.NET_10,
            ),
        ],
        PricingPolicy(score_weights=_PAYMENT_FIT_ONLY),
    )

    assert [score.quote_id for score in comparison.ranked_quotes] == ["NET-0", "NET-10"]
    assert comparison.ranked_quotes[0].payment_term_score == Decimal("100.00")
    assert comparison.ranked_quotes[1].payment_term_score == Decimal("0.00")
