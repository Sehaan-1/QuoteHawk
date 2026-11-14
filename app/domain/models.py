"""Auditable Pydantic contracts for procurement decisions.

The LLM extraction adapter planned for a later PR may propose instances of these
models, but business policy only receives validated objects. Monetary values use
``Decimal`` so cost calculations are reproducible.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Money = Decimal
Score = Decimal


class QuoteSource(StrEnum):
    """The source format from which a supplier quote was normalized."""

    CSV = "csv"
    EMAIL = "email"
    PDF_TEXT = "pdf_text"
    MANUAL = "manual"
    SIMULATOR = "simulator"


class PaymentTerms(StrEnum):
    """Supported payment terms for the first local prototype."""

    NET_10 = "net_10"
    NET_30 = "net_30"
    NET_45 = "net_45"
    NET_60 = "net_60"

    @property
    def days(self) -> int:
        return int(self.value.removeprefix("net_"))


class RequisitionLine(BaseModel):
    """A line requested by the buyer."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    sku: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)
    quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=3)
    unit_of_measure: str = Field(default="each", min_length=1, max_length=20)


class PurchaseRequisition(BaseModel):
    """A buyer's constrained request for supplier quotations."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    requisition_id: str = Field(min_length=1, max_length=80)
    currency: str = Field(min_length=3, max_length=3)
    items: tuple[RequisitionLine, ...] = Field(min_length=1, max_length=50)
    max_landed_cost: Money = Field(ge=0, max_digits=14, decimal_places=2)
    target_lead_time_days: int = Field(ge=0, le=365)
    required_by: date | None = None

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        normalized = value.upper()
        if not normalized.isalpha():
            raise ValueError("currency must be a three-letter ISO-style code")
        return normalized

    @model_validator(mode="after")
    def require_unique_skus(self) -> PurchaseRequisition:
        skus = [item.sku for item in self.items]
        if len(skus) != len(set(skus)):
            raise ValueError("requisition line SKUs must be unique")
        return self


class QuoteLine(BaseModel):
    """A supplier's priced line, normalized into the requisition currency."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    sku: str = Field(min_length=1, max_length=80)
    quoted_quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=3)
    unit_price: Money = Field(ge=0, max_digits=14, decimal_places=4)


class NormalizedQuote(BaseModel):
    """A strict normalized quotation used by deterministic policy code.

    Shipping and duties are intentionally required, including when their value is
    zero. This prevents a parser or supplier from silently omitting a fee.
    """

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    quote_id: str = Field(min_length=1, max_length=100)
    requisition_id: str = Field(min_length=1, max_length=80)
    vendor_id: str = Field(min_length=1, max_length=80)
    vendor_name: str = Field(min_length=1, max_length=160)
    source: QuoteSource
    currency: str = Field(min_length=3, max_length=3)
    lines: tuple[QuoteLine, ...] = Field(min_length=1, max_length=50)
    freight_cost: Money = Field(ge=0, max_digits=14, decimal_places=2)
    duties_cost: Money = Field(ge=0, max_digits=14, decimal_places=2)
    lead_time_days: int = Field(ge=0, le=730)
    payment_terms: PaymentTerms
    minimum_order_quantity: Decimal = Field(default=Decimal("0"), ge=0)
    quality_penalty: Money = Field(default=Decimal("0"), ge=0, max_digits=14)
    early_payment_discount_rate: Decimal = Field(
        default=Decimal("0"), ge=0, le=1, max_digits=5, decimal_places=4
    )
    valid_until: date | None = None

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        normalized = value.upper()
        if not normalized.isalpha():
            raise ValueError("currency must be a three-letter ISO-style code")
        return normalized

    @model_validator(mode="after")
    def require_unique_line_skus(self) -> NormalizedQuote:
        skus = [line.sku for line in self.lines]
        if len(skus) != len(set(skus)):
            raise ValueError("quote line SKUs must be unique")
        return self


class LandedCostBreakdown(BaseModel):
    """Every component used to calculate total landed cost."""

    model_config = ConfigDict(frozen=True)

    currency: str
    line_total: Money = Field(ge=0)
    freight_cost: Money = Field(ge=0)
    duties_cost: Money = Field(ge=0)
    lead_time_penalty: Money = Field(ge=0)
    minimum_order_penalty: Money = Field(ge=0)
    payment_term_penalty: Money = Field(ge=0)
    quality_penalty: Money = Field(ge=0)
    early_payment_discount: Money = Field(ge=0)
    total_landed_cost: Money = Field(ge=0)


class QuoteScore(BaseModel):
    """An explained score, never a hidden recommendation."""

    model_config = ConfigDict(frozen=True)

    quote_id: str
    vendor_id: str
    landed_cost: LandedCostBreakdown
    weighted_score: Score = Field(ge=0, le=100)
    price_score: Score = Field(ge=0, le=100)
    lead_time_score: Score = Field(ge=0, le=100)
    payment_term_score: Score = Field(ge=0, le=100)
    supplier_performance_score: Score = Field(ge=0, le=100)
    approval_ready: bool
    guardrail_messages: tuple[str, ...] = ()


class QuoteComparison(BaseModel):
    """A stable ranking for all received bids on one requisition."""

    model_config = ConfigDict(frozen=True)

    requisition_id: str
    currency: str
    ranked_quotes: tuple[QuoteScore, ...] = Field(min_length=1, max_length=5)


class SupplierObservation(BaseModel):
    """A historical quote/outcome retained for supplier memory."""

    model_config = ConfigDict(frozen=True)

    vendor_id: str = Field(min_length=1, max_length=80)
    quote_id: str = Field(min_length=1, max_length=100)
    observed_on: date
    currency: str = Field(min_length=3, max_length=3)
    average_unit_price: Money = Field(ge=0)
    freight_cost: Money = Field(ge=0)
    delivery_delay_days: int = Field(ge=0, le=730)
    responsiveness_score: Decimal = Field(ge=0, le=1)
    discount_margin: Decimal = Field(ge=0, le=1)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()


class SupplierBaseline(BaseModel):
    """A deterministic aggregate over local supplier observations."""

    model_config = ConfigDict(frozen=True)

    vendor_id: str
    currency: str
    sample_size: int = Field(ge=1)
    average_unit_price: Money = Field(ge=0)
    average_freight_cost: Money = Field(ge=0)
    average_delivery_delay_days: Decimal = Field(ge=0)
    average_responsiveness_score: Decimal = Field(ge=0, le=1)
    average_discount_margin: Decimal = Field(ge=0, le=1)
