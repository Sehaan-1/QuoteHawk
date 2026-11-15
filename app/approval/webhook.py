"""Optional, fail-closed buyer-approval webhook notification.

The integration is a notification channel only. It sends an idempotent, signed
record of a buyer decision and the resulting non-binding draft reference. It has
no ability to send supplier mail, create an ERP order, or alter workflow state.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import NAMESPACE_URL, uuid5

from app.approval.models import (
    ApprovalDecision,
    ApprovalRecord,
    ApprovalWebhookEvent,
    PurchaseOrderDraft,
)
from app.auth.models import AuthMode


class ApprovalWebhookError(RuntimeError):
    """The configured notification webhook could not be delivered safely."""


class ApprovalWebhookTransport(Protocol):
    """Small injectable transport contract for tests and deployment adapters."""

    def post(self, url: str, body: bytes, headers: dict[str, str]) -> int: ...


class UrllibApprovalWebhookTransport:
    """HTTPS POST transport with no retries or redirects hidden from callers."""

    def post(self, url: str, body: bytes, headers: dict[str, str]) -> int:
        request = Request(url, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=5) as response:
                return response.status
        except (HTTPError, URLError) as error:
            raise ApprovalWebhookError("buyer approval webhook delivery failed") from error


@dataclass(frozen=True)
class BuyerApprovalWebhook:
    """Build and dispatch signed, bounded buyer-decision notifications."""

    url: str
    signing_secret: str
    transport: ApprovalWebhookTransport

    @classmethod
    def from_environment(cls) -> BuyerApprovalWebhook | None:
        """Enable only when both explicit endpoint and signing secret are supplied."""

        url = os.getenv("QUOTEHAWK_APPROVAL_WEBHOOK_URL", "").strip()
        secret = os.getenv("QUOTEHAWK_APPROVAL_WEBHOOK_SECRET", "").strip()
        if not url and not secret:
            return None
        if not url or not secret:
            raise ApprovalWebhookError(
                "QUOTEHAWK_APPROVAL_WEBHOOK_URL and QUOTEHAWK_APPROVAL_WEBHOOK_SECRET must be set together"
            )
        if not url.startswith("https://"):
            raise ApprovalWebhookError("buyer approval webhook URL must use HTTPS")
        return cls(url=url, signing_secret=secret, transport=UrllibApprovalWebhookTransport())

    def event_for(
        self,
        *,
        record: ApprovalRecord,
        purchase_order_draft: PurchaseOrderDraft | None,
    ) -> ApprovalWebhookEvent:
        """Create a notification event; no commercial body or vendor contact is included."""

        event_type = (
            "buyer_override" if record.decision is ApprovalDecision.OVERRIDE else "buyer_approval"
        )
        return ApprovalWebhookEvent(
            event_id=uuid5(
                NAMESPACE_URL,
                ":".join(
                    (
                        "quotehawk-buyer-decision",
                        record.actor_tenant_id,
                        record.run_id,
                        record.decision.value,
                        record.decided_at.isoformat(),
                    )
                ),
            ),
            event_type=event_type,
            run_id=record.run_id,
            tenant_id=record.actor_tenant_id,
            buyer_id=record.buyer_id,
            decision=record.decision,
            selected_quote_id=record.selected_quote_id,
            purchase_order_draft_id=(
                purchase_order_draft.draft_id if purchase_order_draft else None
            ),
            occurred_at=datetime.now(UTC),
        )

    def dispatch(self, event: ApprovalWebhookEvent) -> None:
        """Send one signed notification with a deterministic idempotency-key header."""

        body = event.model_dump_json().encode("utf-8")
        signature = hmac.new(self.signing_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        status = self.transport.post(
            self.url,
            body,
            {
                "Content-Type": "application/json",
                "User-Agent": "QuoteHawk/approval-webhook",
                "Idempotency-Key": str(event.event_id),
                "X-QuoteHawk-Signature": f"sha256={signature}",
            },
        )
        if status < 200 or status >= 300:
            raise ApprovalWebhookError("buyer approval webhook returned a non-success status")


def prototype_approval_record(*, run_id: str, tenant_id: str, buyer_id: str) -> ApprovalRecord:
    """Compatibility helper for isolated webhook tests; not used by API routes."""

    from app.approval.models import ApprovalDecision

    return ApprovalRecord(
        run_id=run_id,
        buyer_id=buyer_id,
        actor_subject=buyer_id,
        actor_tenant_id=tenant_id,
        authentication_method=AuthMode.PROTOTYPE,
        decision=ApprovalDecision.APPROVE,
        selected_quote_id="quote-1",
        decided_at=datetime.now(UTC),
    )
