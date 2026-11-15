"""Policy-first buyer approval service for non-binding PO drafts."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from app.approval.models import (
    ApprovalDecision,
    ApprovalRecord,
    ApprovalRequest,
    ApprovalResult,
    BuyerApproval,
    PurchaseOrderDraft,
    PurchaseOrderLine,
)
from app.auth.models import DEFAULT_TENANT_ID, AuthenticatedPrincipal, AuthMode
from app.domain.models import (
    NormalizedQuote,
    PurchaseRequisition,
    QuoteComparison,
    assert_unique_quote_identities,
    persisted_quote_from_payload,
)
from app.persistence.repository import QuoteHawkRepository
from app.workflow.models import RfqRunState


class ApprovalError(ValueError):
    """Raised when an approval attempt breaks a procurement control boundary."""


class BuyerApprovalPolicy:
    """Prototype allowlist; production must replace this with authenticated identity."""

    def __init__(self, authorized_buyer_ids: set[str] | frozenset[str]) -> None:
        normalized = frozenset(
            buyer_id.strip() for buyer_id in authorized_buyer_ids if buyer_id.strip()
        )
        if not normalized:
            raise ValueError("at least one authorized buyer is required")
        self.authorized_buyer_ids = normalized

    @classmethod
    def from_environment(cls) -> BuyerApprovalPolicy:
        values = os.getenv("QUOTEHAWK_AUTHORIZED_BUYERS", "demo-buyer").split(",")
        return cls(set(values))

    def assert_authorized(self, buyer_id: str) -> None:
        if buyer_id not in self.authorized_buyer_ids:
            raise PermissionError("buyer is not authorized to approve this RFQ")


class ApprovalService:
    """Open and resolve a buyer gate without exposing an ERP or messaging tool."""

    def __init__(
        self,
        memory: QuoteHawkRepository,
        *,
        policy: BuyerApprovalPolicy | None = None,
    ) -> None:
        self.memory = memory
        self.policy = policy or BuyerApprovalPolicy.from_environment()

    def open_request(self, run_id: str, *, tenant_id: str = DEFAULT_TENANT_ID) -> ApprovalRequest:
        """Pause an approval-ready RFQ and return the safe buyer-card payload."""

        run = self.memory.load_rfq_run(run_id, tenant_id=tenant_id)
        if run.state is not RfqRunState.NEGOTIATION_OR_READY:
            raise ApprovalError("RFQ run is not ready to request buyer approval")
        self._quotes_for(run_id, tenant_id=tenant_id)
        comparison = self._comparison(run_id, tenant_id=tenant_id)
        eligible_quotes = tuple(score for score in comparison.ranked_quotes if score.approval_ready)
        if not eligible_quotes:
            raise ApprovalError("no within-budget quote is eligible for buyer approval")

        run = self.memory.request_buyer_approval(
            run_id=run_id,
            eligible_quote_ids=tuple(score.quote_id for score in eligible_quotes),
            tenant_id=tenant_id,
        )
        return ApprovalRequest(
            run=run,
            eligible_quotes=eligible_quotes,
            message="Select an eligible supplier or reject. Approval creates a non-binding draft only.",
        )

    def decide(
        self,
        run_id: str,
        buyer_approval: BuyerApproval,
        *,
        actor: AuthenticatedPrincipal | None = None,
    ) -> ApprovalResult:
        """Resolve the gate after authorization and hard-budget validation.

        Direct service callers retain the local allowlist compatibility path. API
        callers provide a verified actor, so an OIDC request body cannot choose the
        recorded approver identity or tenant.
        """

        verified_actor = actor or self._prototype_actor(buyer_approval)
        if verified_actor.auth_mode is AuthMode.PROTOTYPE:
            self.policy.assert_authorized(verified_actor.subject)
        try:
            run = self.memory.load_rfq_run(run_id, tenant_id=verified_actor.tenant_id)
        except KeyError as error:
            raise PermissionError("identity is not authorized for this RFQ tenant") from error
        if run.state is not RfqRunState.AWAITING_APPROVAL:
            raise ApprovalError("RFQ run is not awaiting a buyer decision")

        now = datetime.now(UTC)
        record = ApprovalRecord(
            run_id=run_id,
            buyer_id=verified_actor.subject,
            actor_subject=verified_actor.subject,
            actor_tenant_id=verified_actor.tenant_id,
            authentication_method=verified_actor.auth_mode,
            decision=buyer_approval.decision,
            selected_quote_id=buyer_approval.selected_quote_id,
            note=buyer_approval.note,
            decided_at=now,
        )
        draft = (
            self._build_draft(
                run_id,
                buyer_approval.selected_quote_id,
                verified_actor.subject,
                now,
                tenant_id=verified_actor.tenant_id,
            )
            if buyer_approval.decision is ApprovalDecision.APPROVE
            else None
        )
        self.memory.complete_buyer_approval(approval=record, purchase_order_draft=draft)
        return ApprovalResult(record=record, purchase_order_draft=draft)

    def _prototype_actor(self, buyer_approval: BuyerApproval) -> AuthenticatedPrincipal:
        if buyer_approval.buyer_id is None:
            raise ApprovalError("prototype approval requires a buyer ID")
        return AuthenticatedPrincipal(
            subject=buyer_approval.buyer_id,
            tenant_id=DEFAULT_TENANT_ID,
            auth_mode=AuthMode.PROTOTYPE,
            roles=frozenset(),
        )

    def _build_draft(
        self,
        run_id: str,
        selected_quote_id: str | None,
        buyer_id: str,
        created_at: datetime,
        *,
        tenant_id: str,
    ) -> PurchaseOrderDraft:
        if selected_quote_id is None:  # Protected by BuyerApproval validation.
            raise ApprovalError("approval requires a selected quote")
        comparison = self._comparison(run_id, tenant_id=tenant_id)
        score = next(
            (
                candidate
                for candidate in comparison.ranked_quotes
                if candidate.quote_id == selected_quote_id
            ),
            None,
        )
        if score is None:
            raise ApprovalError("selected quote does not belong to this RFQ run")
        if not score.approval_ready:
            raise ApprovalError("selected quote exceeds the hard landed-cost budget")

        payload = self.memory.payload_for(run_id, tenant_id=tenant_id)
        requisition = PurchaseRequisition.model_validate(payload["requisition"])
        quotes = self._quotes_for(run_id, tenant_id=tenant_id)
        quote = next(
            (candidate for candidate in quotes if candidate.quote_id == selected_quote_id), None
        )
        if quote is None:
            raise ApprovalError("selected quote payload is not available")
        quote_lines = {line.sku: line for line in quote.lines}
        lines = tuple(
            PurchaseOrderLine(
                sku=item.sku,
                description=item.description,
                quantity=item.quantity,
                unit_price=quote_lines[item.sku].unit_price,
            )
            for item in requisition.items
        )
        return PurchaseOrderDraft(
            draft_id=f"pod-{uuid4()}",
            run_id=run_id,
            requisition_id=requisition.requisition_id,
            source_quote_id=quote.quote_id,
            vendor_id=quote.vendor_id,
            vendor_name=quote.vendor_name,
            currency=requisition.currency,
            total_landed_cost=Decimal(score.landed_cost.total_landed_cost),
            lines=lines,
            approved_by=buyer_id,
            created_at=created_at,
        )

    def _quotes_for(self, run_id: str, *, tenant_id: str) -> tuple[NormalizedQuote, ...]:
        """Load legacy snapshots defensively before opening or resolving approval."""

        payload = self.memory.payload_for(run_id, tenant_id=tenant_id)
        quotes = tuple(persisted_quote_from_payload(candidate) for candidate in payload["quotes"])
        try:
            assert_unique_quote_identities(quotes)
        except ValueError as error:
            raise ApprovalError("RFQ run contains ambiguous supplier bid identities") from error
        return quotes

    def _comparison(self, run_id: str, *, tenant_id: str) -> QuoteComparison:
        payload = self.memory.payload_for(run_id, tenant_id=tenant_id)
        if "comparison" not in payload:
            raise ApprovalError("RFQ run has no completed comparison to approve")
        return QuoteComparison.model_validate(payload["comparison"])
