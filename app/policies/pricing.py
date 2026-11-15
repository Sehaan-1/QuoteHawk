"""Deterministic landed-cost and configurable multi-bid scoring policy.

This module deliberately contains no model calls. It is the authority for budget
and math guardrails; LLM output is only accepted after it has become a validated
``NormalizedQuote``.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.models import (
    FxRate,
    LandedCostBreakdown,
    NormalizedQuote,
    PurchaseRequisition,
    QuoteComparison,
    QuoteScore,
)

CENTS = Decimal("0.01")
HUNDRED = Decimal("100")
DEFAULT_PERFORMANCE_SCORE = Decimal("0.50")


class PolicyViolation(ValueError):
    """Raised when a quote is incomplete or incompatible with a requisition."""


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


class ScoreWeights(BaseModel):
    """Weights for the explainable multi-vendor comparison matrix."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    landed_cost: Decimal = Field(default=Decimal("0.60"), ge=0, le=1)
    lead_time: Decimal = Field(default=Decimal("0.15"), ge=0, le=1)
    payment_terms: Decimal = Field(default=Decimal("0.10"), ge=0, le=1)
    supplier_performance: Decimal = Field(default=Decimal("0.15"), ge=0, le=1)

    @model_validator(mode="after")
    def require_complete_weighting(self) -> ScoreWeights:
        if sum(self.model_dump().values()) != Decimal("1.00"):
            raise ValueError("score weights must sum to exactly 1.00")
        return self


class PricingPolicy(BaseModel):
    """Configurable but deterministic rules for true landed cost."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    included_lead_time_days: int | None = Field(default=None, ge=0, le=365)
    lead_time_penalty_per_day: Decimal = Field(default=Decimal("0"), ge=0)
    target_payment_days: int = Field(default=60, ge=0, le=120)
    daily_financing_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    max_quotes_per_run: int = Field(default=20, ge=1, le=50)
    negotiation_threshold_rate: Decimal = Field(default=Decimal("0.15"), gt=0, le=1)
    score_weights: ScoreWeights = Field(default_factory=ScoreWeights)

    @classmethod
    def from_environment(cls) -> PricingPolicy:
        """Load bounded deployment configuration without embedding it in the UI."""

        return cls(
            max_quotes_per_run=int(os.getenv("QUOTEHAWK_MAX_QUOTES_PER_RUN", "20")),
            negotiation_threshold_rate=Decimal(
                os.getenv("QUOTEHAWK_NEGOTIATION_THRESHOLD_RATE", "0.15")
            ),
        )


def _required_line_total(requisition: PurchaseRequisition, quote: NormalizedQuote) -> Decimal:
    quoted_lines = {line.sku: line for line in quote.lines}
    total = Decimal("0")

    for requested_line in requisition.items:
        quoted_line = quoted_lines.get(requested_line.sku)
        if quoted_line is None:
            raise PolicyViolation(
                f"quote {quote.quote_id} is missing requisition SKU {requested_line.sku}"
            )
        if quoted_line.quoted_quantity < requested_line.quantity:
            raise PolicyViolation(
                f"quote {quote.quote_id} quantity for {requested_line.sku} is below "
                f"the requested quantity"
            )
        total += requested_line.quantity * quoted_line.unit_price
    return total


def _conversion_rate(
    quote_currency: str,
    requisition_currency: str,
    fx_rates: tuple[FxRate, ...],
) -> Decimal:
    """Return the explicit rate needed to compare one bid in the PR currency."""

    if quote_currency == requisition_currency:
        return Decimal("1")
    matches = tuple(
        rate
        for rate in fx_rates
        if rate.source_currency == quote_currency and rate.target_currency == requisition_currency
    )
    if len(matches) != 1:
        raise PolicyViolation(
            f"an explicit FX rate from {quote_currency} to {requisition_currency} is required"
        )
    return matches[0].rate


def calculate_landed_cost(
    requisition: PurchaseRequisition,
    quote: NormalizedQuote,
    policy: PricingPolicy,
    *,
    fx_rates: Iterable[FxRate] = (),
) -> LandedCostBreakdown:
    """Calculate landed cost in the PR currency using only explicit FX rates."""

    if quote.requisition_id != requisition.requisition_id:
        raise PolicyViolation("quote does not belong to the requisition")

    rate = _conversion_rate(quote.currency, requisition.currency, tuple(fx_rates))
    line_total = _required_line_total(requisition, quote)
    requested_quantity = sum((line.quantity for line in requisition.items), Decimal("0"))
    average_unit_price = line_total / requested_quantity
    excess_quantity = max(quote.minimum_order_quantity - requested_quantity, Decimal("0"))
    minimum_order_penalty = excess_quantity * average_unit_price
    included_lead_time = (
        requisition.target_lead_time_days
        if policy.included_lead_time_days is None
        else policy.included_lead_time_days
    )
    delayed_days = max(quote.lead_time_days - included_lead_time, 0)
    lead_time_penalty = Decimal(delayed_days) * policy.lead_time_penalty_per_day
    financing_days = max(policy.target_payment_days - quote.effective_payment_days, 0)
    payment_term_penalty = line_total * Decimal(financing_days) * policy.daily_financing_rate
    early_payment_discount = line_total * quote.early_payment_discount_rate

    total = (
        line_total
        + quote.freight_cost
        + quote.shipping_cost
        + quote.duties_cost
        + lead_time_penalty
        + minimum_order_penalty
        + payment_term_penalty
        + quote.quality_penalty
        - early_payment_discount
    )
    if total < 0:
        raise PolicyViolation("discounts cannot reduce landed cost below zero")

    def converted(amount: Decimal) -> Decimal:
        return _money(amount * rate)

    return LandedCostBreakdown(
        currency=requisition.currency,
        source_currency=quote.currency,
        fx_rate=rate,
        line_total=converted(line_total),
        freight_cost=converted(quote.freight_cost),
        shipping_cost=converted(quote.shipping_cost),
        duties_cost=converted(quote.duties_cost),
        lead_time_penalty=converted(lead_time_penalty),
        minimum_order_penalty=converted(minimum_order_penalty),
        payment_term_penalty=converted(payment_term_penalty),
        quality_penalty=converted(quote.quality_penalty),
        early_payment_discount=converted(early_payment_discount),
        total_landed_cost=converted(total),
    )


def _ratio_score(numerator: Decimal, denominator: Decimal) -> Decimal:
    """Return a 0-100 score while handling an all-zero dimension."""

    if denominator == 0:
        return HUNDRED
    return (numerator / denominator) * HUNDRED


def compare_quotes(
    requisition: PurchaseRequisition,
    quotes: list[NormalizedQuote] | tuple[NormalizedQuote, ...],
    policy: PricingPolicy,
    supplier_performance: dict[str, Decimal] | None = None,
    *,
    fx_rates: Iterable[FxRate] = (),
) -> QuoteComparison:
    """Rank a configurable supplier field and expose every guardrail outcome.

    An over-budget quote remains visible to the buyer but can never be marked
    approval-ready. This makes the exception explainable without allowing a
    ranking heuristic to bypass the hard budget boundary.
    """

    if not 1 <= len(quotes) <= policy.max_quotes_per_run:
        raise PolicyViolation(
            f"QuoteHawk compares between one and {policy.max_quotes_per_run} quotes per run"
        )

    normalized_fx_rates = tuple(fx_rates)
    performance = supplier_performance or {}
    breakdowns = [
        calculate_landed_cost(requisition, quote, policy, fx_rates=normalized_fx_rates)
        for quote in quotes
    ]
    lowest_landed_cost = min(item.total_landed_cost for item in breakdowns)
    shortest_lead_time = min(quote.lead_time_days for quote in quotes)
    longest_payment_term = max(quote.effective_payment_days for quote in quotes)
    weights = policy.score_weights

    scored_quotes: list[QuoteScore] = []
    for quote, breakdown in zip(quotes, breakdowns, strict=True):
        price_score = _ratio_score(lowest_landed_cost, breakdown.total_landed_cost)
        lead_time_score = _ratio_score(Decimal(shortest_lead_time), Decimal(quote.lead_time_days))
        payment_term_score = _ratio_score(
            Decimal(quote.effective_payment_days), Decimal(longest_payment_term)
        )
        raw_performance = performance.get(quote.vendor_id, DEFAULT_PERFORMANCE_SCORE)
        if not Decimal("0") <= raw_performance <= Decimal("1"):
            raise PolicyViolation("supplier performance scores must be between 0 and 1")
        performance_score = raw_performance * HUNDRED
        weighted_score = _money(
            (price_score * weights.landed_cost)
            + (lead_time_score * weights.lead_time)
            + (payment_term_score * weights.payment_terms)
            + (performance_score * weights.supplier_performance)
        )

        guardrail_messages: list[str] = []
        approval_ready = breakdown.total_landed_cost <= requisition.max_landed_cost
        if not approval_ready:
            guardrail_messages.append(
                f"Landed cost {breakdown.total_landed_cost} {requisition.currency} exceeds "
                f"requisition budget {requisition.max_landed_cost} {requisition.currency}."
            )

        scored_quotes.append(
            QuoteScore(
                quote_id=quote.quote_id,
                vendor_id=quote.vendor_id,
                landed_cost=breakdown,
                weighted_score=weighted_score,
                price_score=_money(price_score),
                lead_time_score=_money(lead_time_score),
                payment_term_score=_money(payment_term_score),
                supplier_performance_score=_money(performance_score),
                approval_ready=approval_ready,
                guardrail_messages=tuple(guardrail_messages),
            )
        )

    ranked_quotes = tuple(
        sorted(
            scored_quotes,
            key=lambda score: (
                not score.approval_ready,
                -score.weighted_score,
                score.landed_cost.total_landed_cost,
                score.quote_id,
            ),
        )
    )
    return QuoteComparison(
        requisition_id=requisition.requisition_id,
        currency=requisition.currency,
        ranked_quotes=ranked_quotes,
    )
