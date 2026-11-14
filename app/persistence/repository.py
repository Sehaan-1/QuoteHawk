"""Small persistence contracts so SQLite can later be replaced by Postgres."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, Protocol

from app.analysis.models import SupplierQuoteBaseline
from app.approval.models import ApprovalRecord, PurchaseOrderDraft
from app.domain.models import NormalizedQuote, SupplierBaseline, SupplierObservation
from app.workflow.models import RfqAuditEvent, RfqRun, RfqRunState


class SupplierMemoryRepository(Protocol):
    """Historical supplier data needed by policy and anomaly analysis."""

    def record_observation(self, observation: SupplierObservation) -> None:
        """Upsert one auditable supplier performance observation."""

    def baseline_for(self, vendor_id: str, currency: str) -> SupplierBaseline | None:
        """Return a deterministic vendor performance baseline, if present."""

    def record_quote_history(self, quote: NormalizedQuote, observed_on: date) -> None:
        """Persist a commercial bid after it is analyzed."""

    def quote_baseline_for(
        self,
        vendor_id: str,
        currency: str,
        *,
        as_of: date,
        lookback_days: int,
    ) -> SupplierQuoteBaseline | None:
        """Return prior commercial history excluding the current quote."""


class RfqRunRepository(Protocol):
    """Durable state/event persistence required by the explicit workflow."""

    def create_rfq_run(
        self, *, run_id: str, requisition_id: str, payload: Mapping[str, Any]
    ) -> RfqRun:
        """Create an intake run."""

    def advance_rfq_run(
        self,
        *,
        run_id: str,
        to_state: RfqRunState,
        event_type: str,
        reason: str,
        details: Mapping[str, Any] | None = None,
        payload_patch: Mapping[str, Any] | None = None,
    ) -> RfqRun:
        """Persist one valid state transition and audit event."""

    def request_buyer_approval(self, *, run_id: str, eligible_quote_ids: tuple[str, ...]) -> RfqRun:
        """Persist an approval pause after deterministic eligibility checks."""

    def complete_buyer_approval(
        self,
        *,
        approval: ApprovalRecord,
        purchase_order_draft: PurchaseOrderDraft | None,
    ) -> RfqRun:
        """Atomically persist a buyer decision and optional non-binding draft."""

    def approval_for(self, run_id: str) -> ApprovalRecord | None:
        """Return the persisted buyer decision, if any."""

    def purchase_order_draft_for(self, run_id: str) -> PurchaseOrderDraft | None:
        """Return the non-binding PO-draft payload, if approved."""

    def audit_events_for(self, run_id: str) -> tuple[RfqAuditEvent, ...]:
        """Return the ordered audit trail."""
