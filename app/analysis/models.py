"""Contracts for explainable supplier-quote baselines and anomalies."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class AnomalyMetric(StrEnum):
    """Commercial dimensions compared against a vendor's own history."""

    UNIT_PRICE = "unit_price"
    FREIGHT_COST = "freight_cost"
    LEAD_TIME_DAYS = "lead_time_days"


class AnomalySeverity(StrEnum):
    """Severity is deterministic and based on the configured threshold."""

    MEDIUM = "medium"
    HIGH = "high"


class SupplierQuoteBaseline(BaseModel):
    """Rolling commercial baseline calculated before the current bid is stored."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    vendor_id: str
    currency: str
    sample_size: int = Field(ge=1)
    window_start: date
    as_of: date
    average_unit_price: Decimal = Field(ge=0)
    average_freight_cost: Decimal = Field(ge=0)
    average_lead_time_days: Decimal = Field(ge=0)
    average_payment_term_days: Decimal = Field(ge=0)


class QuoteAnomaly(BaseModel):
    """One reproducible deviation between a bid and supplier history."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    quote_id: str
    vendor_id: str
    currency: str
    metric: AnomalyMetric
    severity: AnomalySeverity
    observed_value: Decimal = Field(ge=0)
    baseline_value: Decimal = Field(ge=0)
    deviation_rate: Decimal = Field(gt=0)
    threshold_rate: Decimal = Field(gt=0)
    observed_on: date
    explanation: str = Field(min_length=1, max_length=500)
