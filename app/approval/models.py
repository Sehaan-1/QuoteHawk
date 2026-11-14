"""Pydantic contracts for the buyer-controlled PO-draft gate."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.models import QuoteScore
from app.workflow.models import RfqRun


class ApprovalDecision(StrEnum):
    """The only decisions a buyer can submit to a pending request."""

    APPROVE = "approve"
    REJECT = "reject"


class BuyerApproval(BaseModel):
    """Buyer input required to resolve an awaiting-approval run."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    buyer_id: str = Field(min_length=3, max_length=160)
    decision: ApprovalDecision
    selected_quote_id: str | None = Field(default=None, min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_a_quote_only_for_approval(self) -> BuyerApproval:
        if self.decision is ApprovalDecision.APPROVE and self.selected_quote_id is None:
            raise ValueError("an approved PO draft requires a selected quote")
        if self.decision is ApprovalDecision.REJECT and self.selected_quote_id is not None:
            raise ValueError("a rejected approval cannot select a quote")
        return self


class ApprovalRequest(BaseModel):
    """Safe approval-card payload for the future web/mobile client."""

    model_config = ConfigDict(frozen=True)

    run: RfqRun
    eligible_quotes: tuple[QuoteScore, ...] = Field(min_length=1)
    message: str


class ApprovalRecord(BaseModel):
    """Immutable buyer decision stored alongside the RFQ audit trail."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    buyer_id: str
    decision: ApprovalDecision
    selected_quote_id: str | None = None
    note: str | None = None
    decided_at: datetime


class PurchaseOrderLine(BaseModel):
    """A non-binding PO-draft line derived from validated requisition/quote data."""

    model_config = ConfigDict(frozen=True)

    sku: str
    description: str
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)


class PurchaseOrderDraft(BaseModel):
    """A buyer-approved draft; it is intentionally not an executable order."""

    model_config = ConfigDict(frozen=True)

    draft_id: str
    run_id: str
    requisition_id: str
    source_quote_id: str
    vendor_id: str
    vendor_name: str
    currency: str
    total_landed_cost: Decimal = Field(ge=0)
    lines: tuple[PurchaseOrderLine, ...] = Field(min_length=1)
    approved_by: str
    created_at: datetime
    status: Literal["draft_only"] = "draft_only"
    disclaimer: str = (
        "Non-binding draft only. QuoteHawk does not transmit this payload to an ERP or supplier."
    )


class ApprovalResult(BaseModel):
    """Result returned after the controlled approval/rejection transition."""

    model_config = ConfigDict(frozen=True)

    record: ApprovalRecord
    purchase_order_draft: PurchaseOrderDraft | None = None
