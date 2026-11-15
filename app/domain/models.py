"""Auditable Pydantic contracts for procurement decisions.

The LLM extraction adapter planned for a later PR may propose instances of these
models, but business policy only receives validated objects. Monetary values use
``Decimal`` so cost calculations are reproducible.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Money = Decimal
Score = Decimal


class QuoteSource(StrEnum):
    """The source format from which a supplier quote was normalized."""

    CSV = "csv"
    EMAIL = "email"
    PDF = "pdf"
    PDF_TEXT = "pdf_text"
    MANUAL = "manual"
    SIMULATOR = "simulator"


class PaymentTerms(StrEnum):
    """Commercial payment structures accepted by deterministic policy."""

    NET_0 = "net_0"
    NET_10 = "net_10"
    NET_15 = "net_15"
    NET_30 = "net_30"
    NET_45 = "net_45"
    NET_60 = "net_60"
    NET_90 = "net_90"
    NET_120 = "net_120"
    PREPAYMENT = "prepayment"
    MILESTONE = "milestone"
    LETTER_OF_CREDIT = "letter_of_credit"

    @property
    def days(self) -> int:
        """Return the fixed timing for net/prepayment terms only."""

        if self.value.startswith("net_"):
            return int(self.value.removeprefix("net_"))
        if self is PaymentTerms.PREPAYMENT:
            return 0
        raise ValueError("structured payment terms require NormalizedQuote.effective_payment_days")

    @property
    def has_fixed_days(self) -> bool:
        return self.value.startswith("net_") or self is PaymentTerms.PREPAYMENT


class PaymentMilestone(BaseModel):
    """One declared portion of a milestone-billed supplier quotation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    percentage: Decimal = Field(gt=0, le=1, max_digits=5, decimal_places=4)
    due_days: int = Field(ge=0, le=730)
    description: str | None = Field(default=None, min_length=1, max_length=120)


class FxRate(BaseModel):
    """An explicit auditable rate from a quote currency into the PR currency."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")

    source_currency: str = Field(min_length=3, max_length=3)
    target_currency: str = Field(min_length=3, max_length=3)
    rate: Decimal = Field(gt=0, max_digits=14, decimal_places=8)
    as_of: date | None = None
    source: str = Field(default="buyer_supplied", min_length=1, max_length=80)

    @field_validator("source_currency", "target_currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        normalized = value.upper()
        if not normalized.isalpha():
            raise ValueError("currency must be a three-letter ISO-style code")
        return normalized

    @model_validator(mode="after")
    def require_distinct_currencies(self) -> FxRate:
        if self.source_currency == self.target_currency:
            raise ValueError("FX rates must convert between distinct currencies")
        return self


class RequisitionLine(BaseModel):
    """A line requested by the buyer."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")

    sku: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)
    quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=3)
    unit_of_measure: str = Field(default="each", min_length=1, max_length=20)


class PurchaseRequisition(BaseModel):
    """A buyer's constrained request for supplier quotations."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")

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
    """A supplier-priced line expressed in its quote currency."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")

    sku: str = Field(min_length=1, max_length=80)
    quoted_quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=3)
    unit_price: Money = Field(ge=0, max_digits=14, decimal_places=4)


class NormalizedQuote(BaseModel):
    """A strict normalized quotation used by deterministic policy code.

    Freight, shipping, and duties are intentionally required, including when each
    value is zero. This prevents a parser or supplier from silently omitting a fee.
    """

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")

    quote_id: str = Field(min_length=1, max_length=100)
    requisition_id: str = Field(min_length=1, max_length=80)
    vendor_id: str = Field(min_length=1, max_length=80)
    vendor_name: str = Field(min_length=1, max_length=160)
    source: QuoteSource
    currency: str = Field(min_length=3, max_length=3)
    lines: tuple[QuoteLine, ...] = Field(min_length=1, max_length=50)
    freight_cost: Money = Field(ge=0, max_digits=14, decimal_places=2)
    shipping_cost: Money = Field(ge=0, max_digits=14, decimal_places=2)
    duties_cost: Money = Field(ge=0, max_digits=14, decimal_places=2)
    lead_time_days: int = Field(ge=0, le=730)
    payment_terms: PaymentTerms
    payment_term_days: int | None = Field(default=None, ge=0, le=730)
    payment_milestones: tuple[PaymentMilestone, ...] = Field(default=(), max_length=12)
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
    def require_valid_commercial_structure(self) -> NormalizedQuote:
        skus = [line.sku for line in self.lines]
        if len(skus) != len(set(skus)):
            raise ValueError("quote line SKUs must be unique")
        if self.payment_terms.has_fixed_days:
            if self.payment_term_days is not None or self.payment_milestones:
                raise ValueError("fixed payment terms cannot include structured payment timing")
        elif self.payment_terms is PaymentTerms.LETTER_OF_CREDIT:
            if self.payment_term_days is None:
                raise ValueError("letter_of_credit payment terms require payment_term_days")
            if self.payment_milestones:
                raise ValueError("letter_of_credit payment terms cannot include milestones")
        elif self.payment_terms is PaymentTerms.MILESTONE:
            if not self.payment_milestones:
                raise ValueError("milestone payment terms require payment_milestones")
            if self.payment_term_days is not None:
                raise ValueError("milestone payment terms derive timing from payment_milestones")
            if sum(
                (milestone.percentage for milestone in self.payment_milestones), Decimal("0")
            ) != Decimal("1"):
                raise ValueError("payment milestone percentages must sum to 1.00")
        return self

    @property
    def effective_payment_days(self) -> int:
        """Return deterministic financing timing for fixed, LC, or milestone terms."""

        if self.payment_terms.has_fixed_days:
            return self.payment_terms.days
        if self.payment_terms is PaymentTerms.LETTER_OF_CREDIT:
            if self.payment_term_days is None:  # guarded by model validation
                raise ValueError("letter_of_credit payment terms require payment_term_days")
            return self.payment_term_days
        weighted_days = sum(
            (
                milestone.percentage * Decimal(milestone.due_days)
                for milestone in self.payment_milestones
            ),
            Decimal("0"),
        )
        return int(weighted_days.to_integral_value(rounding=ROUND_HALF_UP))


def persisted_quote_from_payload(payload: Mapping[str, object]) -> NormalizedQuote:
    """Read a durable quote snapshot while preserving the pre-shipping schema.

    Releases before the explicit ``shipping_cost`` component represented shipping
    within freight. New HTTP/source inputs must include shipping, but existing
    draft-only workflow snapshots stay resumable with their documented legacy
    zero-separate-shipping interpretation.
    """

    if "shipping_cost" not in payload:
        return NormalizedQuote.model_validate({**payload, "shipping_cost": "0"})
    return NormalizedQuote.model_validate(payload)


def assert_unique_quote_identities(quotes: Iterable[NormalizedQuote]) -> None:
    """Reject ambiguous supplier options before scoring, approval, or persistence.

    A duplicate quote ID can make a persisted score point at a different payload,
    while duplicate vendor IDs can make a counter-offer target ambiguous. This
    prototype therefore accepts one normalized bid per stable supplier identity.
    """

    quote_ids: set[str] = set()
    vendor_ids: set[str] = set()
    for quote in quotes:
        quote_id = unicodedata.normalize("NFKC", quote.quote_id).casefold()
        vendor_id = unicodedata.normalize("NFKC", quote.vendor_id).casefold()
        if quote_id in quote_ids:
            raise ValueError("quote IDs must be unique within an RFQ submission")
        if vendor_id in vendor_ids:
            raise ValueError("vendor IDs must be unique within an RFQ submission")
        quote_ids.add(quote_id)
        vendor_ids.add(vendor_id)


class LandedCostBreakdown(BaseModel):
    """Every component used to calculate total landed cost in the PR currency."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    currency: str
    source_currency: str
    fx_rate: Decimal = Field(default=Decimal("1"), gt=0)
    line_total: Money = Field(ge=0)

    @model_validator(mode="before")
    @classmethod
    def preserve_legacy_same_currency_snapshots(cls, value: object) -> object:
        if isinstance(value, Mapping) and "source_currency" not in value:
            return {**value, "source_currency": value.get("currency", "USD")}
        return value

    freight_cost: Money = Field(ge=0)
    shipping_cost: Money = Field(ge=0)
    duties_cost: Money = Field(ge=0)
    lead_time_penalty: Money = Field(ge=0)
    minimum_order_penalty: Money = Field(ge=0)
    payment_term_penalty: Money = Field(ge=0)
    quality_penalty: Money = Field(ge=0)
    early_payment_discount: Money = Field(ge=0)
    total_landed_cost: Money = Field(ge=0)


class QuoteScore(BaseModel):
    """An explained score, never a hidden recommendation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

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

    model_config = ConfigDict(frozen=True, extra="forbid")

    requisition_id: str
    currency: str
    ranked_quotes: tuple[QuoteScore, ...] = Field(min_length=1, max_length=50)


class SupplierObservation(BaseModel):
    """A historical quote/outcome retained for supplier memory."""

    model_config = ConfigDict(frozen=True, extra="forbid")

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

    model_config = ConfigDict(frozen=True, extra="forbid")

    vendor_id: str
    currency: str
    sample_size: int = Field(ge=1)
    average_unit_price: Money = Field(ge=0)
    average_freight_cost: Money = Field(ge=0)
    average_delivery_delay_days: Decimal = Field(ge=0)
    average_responsiveness_score: Decimal = Field(ge=0, le=1)
    average_discount_margin: Decimal = Field(ge=0, le=1)
