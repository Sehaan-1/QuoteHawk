"""Pure, deterministic value-leakage detection against supplier history."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.analysis.models import (
    AnomalyMetric,
    AnomalySeverity,
    QuoteAnomaly,
    SupplierQuoteBaseline,
)
from app.domain.models import NormalizedQuote

PERCENT = Decimal("0.0001")


class AnomalyPolicy(BaseModel):
    """Thresholds for positive supplier deviations from rolling history."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lookback_days: int = Field(default=180, ge=30, le=730)
    minimum_history: int = Field(default=2, ge=1, le=100)
    unit_price_deviation_threshold: Decimal = Field(default=Decimal("0.15"), gt=0, le=5)
    freight_deviation_threshold: Decimal = Field(default=Decimal("0.20"), gt=0, le=5)
    lead_time_deviation_threshold: Decimal = Field(default=Decimal("0.50"), gt=0, le=5)


def average_quote_unit_price(quote: NormalizedQuote) -> Decimal:
    """Return the quantity-weighted unit price used for comparable history."""

    quantity = sum((line.quoted_quantity for line in quote.lines), Decimal("0"))
    if quantity == 0:  # Defensive; strict Pydantic schemas already forbid this.
        raise ValueError("quote lines must have positive quoted quantities")
    line_total = sum((line.quoted_quantity * line.unit_price for line in quote.lines), Decimal("0"))
    return line_total / quantity


def _severity(deviation_rate: Decimal, threshold_rate: Decimal) -> AnomalySeverity:
    return (
        AnomalySeverity.HIGH
        if deviation_rate >= threshold_rate * Decimal("2")
        else AnomalySeverity.MEDIUM
    )


def _finding(
    *,
    quote: NormalizedQuote,
    metric: AnomalyMetric,
    observed_value: Decimal,
    baseline_value: Decimal,
    threshold_rate: Decimal,
    observed_on: date,
) -> QuoteAnomaly | None:
    """Build an anomaly only when a current commercial value exceeds history."""

    if baseline_value == 0 or observed_value <= baseline_value:
        return None
    deviation_rate = (observed_value - baseline_value) / baseline_value
    if deviation_rate <= threshold_rate:
        return None

    percentage = (deviation_rate * Decimal("100")).quantize(PERCENT, rounding=ROUND_HALF_UP)
    baseline = baseline_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    observed = observed_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    label = metric.value.replace("_", " ")
    return QuoteAnomaly(
        quote_id=quote.quote_id,
        vendor_id=quote.vendor_id,
        currency=quote.currency,
        metric=metric,
        severity=_severity(deviation_rate, threshold_rate),
        observed_value=observed_value,
        baseline_value=baseline_value,
        deviation_rate=deviation_rate,
        threshold_rate=threshold_rate,
        observed_on=observed_on,
        explanation=(
            f"{label.title()} is {percentage}% above this supplier's rolling baseline "
            f"({observed} {quote.currency} vs {baseline} {quote.currency})."
        ),
    )


def detect_quote_anomalies(
    quote: NormalizedQuote,
    baseline: SupplierQuoteBaseline | None,
    policy: AnomalyPolicy,
    observed_on: date,
) -> tuple[QuoteAnomaly, ...]:
    """Flag positive cost/lead-time deviations without modifying a bid or score."""

    if baseline is None or baseline.sample_size < policy.minimum_history:
        return ()
    if baseline.vendor_id != quote.vendor_id or baseline.currency != quote.currency:
        raise ValueError("baseline must match the quote vendor and currency")

    candidates = (
        _finding(
            quote=quote,
            metric=AnomalyMetric.UNIT_PRICE,
            observed_value=average_quote_unit_price(quote),
            baseline_value=baseline.average_unit_price,
            threshold_rate=policy.unit_price_deviation_threshold,
            observed_on=observed_on,
        ),
        _finding(
            quote=quote,
            metric=AnomalyMetric.FREIGHT_COST,
            observed_value=quote.freight_cost,
            baseline_value=baseline.average_freight_cost,
            threshold_rate=policy.freight_deviation_threshold,
            observed_on=observed_on,
        ),
        _finding(
            quote=quote,
            metric=AnomalyMetric.LEAD_TIME_DAYS,
            observed_value=Decimal(quote.lead_time_days),
            baseline_value=baseline.average_lead_time_days,
            threshold_rate=policy.lead_time_deviation_threshold,
            observed_on=observed_on,
        ),
    )
    return tuple(finding for finding in candidates if finding is not None)
