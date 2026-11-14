"""Persistence contracts shared by the local SQLite and Postgres backends."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, Protocol

from app.analysis.models import SupplierQuoteBaseline
from app.approval.models import ApprovalRecord, PurchaseOrderDraft
from app.auth.models import DEFAULT_TENANT_ID
from app.domain.models import NormalizedQuote, SupplierBaseline, SupplierObservation
from app.workflow.models import RfqAuditEvent, RfqRun, RfqRunState


class SupplierMemoryRepository(Protocol):
    """Tenant-scoped historical supplier data needed by policy/anomaly analysis."""

    def record_observation(
        self, observation: SupplierObservation, *, tenant_id: str = DEFAULT_TENANT_ID
    ) -> None:
        """Upsert one auditable supplier performance observation."""

    def baseline_for(
        self, vendor_id: str, currency: str, *, tenant_id: str = DEFAULT_TENANT_ID
    ) -> SupplierBaseline | None:
        """Return a deterministic vendor performance baseline, if present."""

    def record_quote_history(
        self, quote: NormalizedQuote, observed_on: date, *, tenant_id: str = DEFAULT_TENANT_ID
    ) -> None:
        """Persist a commercial bid after it is analyzed."""

    def quote_baseline_for(
        self,
        vendor_id: str,
        currency: str,
        *,
        tenant_id: str = DEFAULT_TENANT_ID,
        as_of: date,
        lookback_days: int,
    ) -> SupplierQuoteBaseline | None:
        """Return prior commercial history excluding the current quote."""


class RfqRunRepository(Protocol):
    """Tenant-scoped durable state/event persistence for the explicit workflow."""

    def create_rfq_run(
        self,
        *,
        run_id: str,
        requisition_id: str,
        tenant_id: str = DEFAULT_TENANT_ID,
        payload: Mapping[str, Any],
    ) -> RfqRun:
        """Create an intake run."""

    def advance_rfq_run(
        self,
        *,
        run_id: str,
        to_state: RfqRunState,
        event_type: str,
        reason: str,
        tenant_id: str = DEFAULT_TENANT_ID,
        details: Mapping[str, Any] | None = None,
        payload_patch: Mapping[str, Any] | None = None,
    ) -> RfqRun:
        """Persist one valid state transition and audit event for one tenant's run."""

    def request_buyer_approval(
        self,
        *,
        run_id: str,
        eligible_quote_ids: tuple[str, ...],
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> RfqRun:
        """Persist an approval pause after deterministic eligibility checks."""

    def complete_buyer_approval(
        self,
        *,
        approval: ApprovalRecord,
        purchase_order_draft: PurchaseOrderDraft | None,
    ) -> RfqRun:
        """Atomically persist a buyer decision and optional non-binding draft."""

    def approval_for(
        self, run_id: str, *, tenant_id: str = DEFAULT_TENANT_ID
    ) -> ApprovalRecord | None:
        """Return the persisted buyer decision within a tenant, if any."""

    def purchase_order_draft_for(
        self, run_id: str, *, tenant_id: str = DEFAULT_TENANT_ID
    ) -> PurchaseOrderDraft | None:
        """Return the non-binding PO-draft payload within a tenant, if approved."""

    def record_rfq_event(
        self,
        *,
        run_id: str,
        event_type: str,
        reason: str,
        tenant_id: str = DEFAULT_TENANT_ID,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        """Append a non-transition event for one tenant's RFQ run."""

    def load_rfq_run(self, run_id: str, *, tenant_id: str = DEFAULT_TENANT_ID) -> RfqRun:
        """Load a tenant-owned RFQ run."""

    def audit_events_for(
        self, run_id: str, *, tenant_id: str = DEFAULT_TENANT_ID
    ) -> tuple[RfqAuditEvent, ...]:
        """Return the ordered tenant-owned audit trail."""

    def payload_for(self, run_id: str, *, tenant_id: str = DEFAULT_TENANT_ID) -> dict[str, Any]:
        """Return the tenant-owned JSON-safe workflow snapshot."""


class QuoteHawkRepository(SupplierMemoryRepository, RfqRunRepository, Protocol):
    """Complete storage surface consumed by QuoteHawk services."""
