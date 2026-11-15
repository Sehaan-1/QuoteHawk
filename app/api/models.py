"""HTTP request/response contracts that expose business facts, not hidden reasoning."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.approval.models import ApprovalRecord, PurchaseOrderDraft
from app.domain.models import (
    FxRate,
    NormalizedQuote,
    PurchaseRequisition,
    assert_unique_quote_identities,
)
from app.ingestion.documents import SupplierQuoteDocument
from app.ingestion.extractors import ExtractionProvider
from app.workflow.models import RfqAuditEvent, RfqRun, RfqWorkflowResult


class RfqRunSubmission(BaseModel):
    """Validated input for one local RFQ workflow invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    requisition: PurchaseRequisition
    quotes: tuple[NormalizedQuote, ...] = Field(min_length=1, max_length=50)
    fx_rates: tuple[FxRate, ...] = Field(default=(), max_length=50)
    observed_on: date | None = None
    run_id: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def require_unique_quote_identities(self) -> RfqRunSubmission:
        assert_unique_quote_identities(self.quotes)
        pairs = {(rate.source_currency, rate.target_currency) for rate in self.fx_rates}
        if len(pairs) != len(self.fx_rates):
            raise ValueError("FX rates must not repeat a currency conversion pair")
        return self


class RfqSourceRunSubmission(BaseModel):
    """A three-to-fifty-bid RFQ submitted as source artifacts, not prebuilt quotes.

    The API hashes source material for idempotency but only persists the resulting
    normalized quotes and policy output. Raw emails, CSV bodies, and PDF data are
    not retained in RFQ snapshots or application logs.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    requisition: PurchaseRequisition
    supplier_documents: tuple[SupplierQuoteDocument, ...] = Field(min_length=3, max_length=50)
    fx_rates: tuple[FxRate, ...] = Field(default=(), max_length=50)
    provider: ExtractionProvider = ExtractionProvider.RULE_BASED
    observed_on: date | None = None
    run_id: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def require_unique_fx_pairs(self) -> RfqSourceRunSubmission:
        pairs = {(rate.source_currency, rate.target_currency) for rate in self.fx_rates}
        if len(pairs) != len(self.fx_rates):
            raise ValueError("FX rates must not repeat a currency conversion pair")
        return self


class RfqRunDetail(BaseModel):
    """Resumable workflow view for the future dashboard and mobile approval card."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run: RfqRun
    audit_events: tuple[RfqAuditEvent, ...]
    approval: ApprovalRecord | None = None
    purchase_order_draft: PurchaseOrderDraft | None = None
    payload: dict[str, Any]


class QuoteExtractionRequest(BaseModel):
    """Untrusted source text and an explicit provider choice."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    raw_quote: str = Field(min_length=1, max_length=50_000)
    provider: ExtractionProvider = ExtractionProvider.FIXTURE


__all__ = [
    "QuoteExtractionRequest",
    "RfqRunDetail",
    "RfqRunSubmission",
    "RfqSourceRunSubmission",
    "RfqWorkflowResult",
]
