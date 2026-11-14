"""HTTP request/response contracts that expose business facts, not hidden reasoning."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.approval.models import ApprovalRecord, PurchaseOrderDraft
from app.domain.models import NormalizedQuote, PurchaseRequisition
from app.ingestion.extractors import ExtractionProvider
from app.workflow.models import RfqAuditEvent, RfqRun, RfqWorkflowResult


class RfqRunSubmission(BaseModel):
    """Validated input for one local RFQ workflow invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    requisition: PurchaseRequisition
    quotes: tuple[NormalizedQuote, ...] = Field(min_length=1, max_length=5)
    observed_on: date | None = None
    run_id: str | None = Field(default=None, min_length=1, max_length=100)


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
    "RfqWorkflowResult",
]
