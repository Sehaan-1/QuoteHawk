"""Pydantic contracts for the buyer-controlled PO-draft gate."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.auth.models import AuthMode
from app.domain.models import QuoteScore
from app.workflow.models import RfqRun


class ApprovalDecision(StrEnum):
    """The only decisions a buyer can submit to a pending request."""

    APPROVE = "approve"
    REJECT = "reject"


class BuyerApproval(BaseModel):
    """Buyer input required to resolve an awaiting-approval run."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")

    # Deprecated local-prototype input. In OIDC mode it is ignored; the server
    # records the verified token subject instead.
    buyer_id: str | None = Field(default=None, min_length=3, max_length=160)
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

    model_config = ConfigDict(frozen=True, extra="forbid")

    run: RfqRun
    eligible_quotes: tuple[QuoteScore, ...] = Field(min_length=1)
    message: str


class ApprovalRecord(BaseModel):
    """Immutable buyer decision stored alongside the RFQ audit trail."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    # buyer_id remains the stable display/key field; it is always actor_subject
    # once the API supplies a verified principal.
    buyer_id: str
    actor_subject: str = Field(min_length=1, max_length=255)
    actor_tenant_id: str = Field(min_length=1, max_length=120)
    authentication_method: AuthMode
    decision: ApprovalDecision
    selected_quote_id: str | None = None
    note: str | None = None
    decided_at: datetime

    @model_validator(mode="after")
    def require_server_actor_display_consistency(self) -> ApprovalRecord:
        """Prevent a durable record from presenting a different buyer than its actor."""

        if self.buyer_id != self.actor_subject:
            raise ValueError("approval buyer_id must match the authenticated actor subject")
        return self


class PurchaseOrderLine(BaseModel):
    """A non-binding PO-draft line derived from validated requisition/quote data."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str
    description: str
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)


class PurchaseOrderDraft(BaseModel):
    """A buyer-approved draft; it is intentionally not an executable order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

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

    model_config = ConfigDict(frozen=True, extra="forbid")

    record: ApprovalRecord
    purchase_order_draft: PurchaseOrderDraft | None = None
