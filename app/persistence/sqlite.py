"""SQLite-backed supplier memory and durable RFQ audit trail."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.analysis.models import SupplierQuoteBaseline
from app.approval.models import (
    ApprovalDecision,
    ApprovalRecord,
    PurchaseOrderDraft,
)
from app.auth.models import DEFAULT_TENANT_ID, AuthMode
from app.domain.models import (
    NormalizedQuote,
    QuoteComparison,
    SupplierBaseline,
    SupplierObservation,
)
from app.workflow.models import RfqAuditEvent, RfqRun, RfqRunState
from app.workflow.state_machine import InvalidStateTransition, assert_transition


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _average_quote_unit_price(quote: NormalizedQuote) -> Decimal:
    quantity = sum((line.quoted_quantity for line in quote.lines), Decimal("0"))
    line_total = sum((line.quoted_quantity * line.unit_price for line in quote.lines), Decimal("0"))
    return line_total / quantity


class SqliteSupplierMemory:
    """Persist exact supplier history and replayable, concise RFQ audit events.

    Monetary data is written as canonical decimal text rather than SQLite REAL.
    Current quotes are recorded *after* they have been analyzed, so a bid cannot
    dilute its own historical baseline during the same workflow run.
    """

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        self.initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS supplier_observations (
                    tenant_id TEXT NOT NULL,
                    vendor_id TEXT NOT NULL,
                    quote_id TEXT NOT NULL,
                    observed_on TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    average_unit_price TEXT NOT NULL,
                    freight_cost TEXT NOT NULL,
                    delivery_delay_days INTEGER NOT NULL,
                    responsiveness_score TEXT NOT NULL,
                    discount_margin TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, vendor_id, quote_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS supplier_quote_history (
                    tenant_id TEXT NOT NULL,
                    vendor_id TEXT NOT NULL,
                    quote_id TEXT NOT NULL,
                    observed_on TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    average_unit_price TEXT NOT NULL,
                    freight_cost TEXT NOT NULL,
                    lead_time_days INTEGER NOT NULL,
                    payment_term_days INTEGER NOT NULL,
                    PRIMARY KEY (tenant_id, vendor_id, quote_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS rfq_runs (
                    run_id TEXT PRIMARY KEY,
                    requisition_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS rfq_run_events (
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    occurred_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    from_state TEXT,
                    to_state TEXT,
                    reason TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, sequence),
                    FOREIGN KEY (run_id) REFERENCES rfq_runs(run_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS rfq_approvals (
                    run_id TEXT PRIMARY KEY,
                    buyer_id TEXT NOT NULL,
                    actor_subject TEXT NOT NULL,
                    actor_tenant_id TEXT NOT NULL,
                    authentication_method TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    selected_quote_id TEXT,
                    note TEXT,
                    decided_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES rfq_runs(run_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS purchase_order_drafts (
                    run_id TEXT PRIMARY KEY,
                    draft_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES rfq_runs(run_id)
                )
                """
            )
            self._migrate_identity_schema(connection)
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_supplier_observations_tenant_vendor_currency
                ON supplier_observations (tenant_id, vendor_id, currency, observed_on)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_supplier_quote_history_tenant_window
                ON supplier_quote_history (tenant_id, vendor_id, currency, observed_on)
                """
            )

    @staticmethod
    def _has_column(connection: sqlite3.Connection, table_name: str, column_name: str) -> bool:
        return any(
            row["name"] == column_name
            for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        )

    @classmethod
    def _migrate_identity_schema(cls, connection: sqlite3.Connection) -> None:
        """Upgrade local PR-5 databases without mixing tenant commercial history."""

        if not cls._has_column(connection, "supplier_observations", "tenant_id"):
            connection.execute(
                "ALTER TABLE supplier_observations RENAME TO supplier_observations_legacy"
            )
            connection.execute(
                """
                CREATE TABLE supplier_observations (
                    tenant_id TEXT NOT NULL,
                    vendor_id TEXT NOT NULL,
                    quote_id TEXT NOT NULL,
                    observed_on TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    average_unit_price TEXT NOT NULL,
                    freight_cost TEXT NOT NULL,
                    delivery_delay_days INTEGER NOT NULL,
                    responsiveness_score TEXT NOT NULL,
                    discount_margin TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, vendor_id, quote_id)
                )
                """
            )
            connection.execute(
                """
                INSERT INTO supplier_observations (
                    tenant_id, vendor_id, quote_id, observed_on, currency, average_unit_price,
                    freight_cost, delivery_delay_days, responsiveness_score, discount_margin
                )
                SELECT ?, vendor_id, quote_id, observed_on, currency, average_unit_price,
                       freight_cost, delivery_delay_days, responsiveness_score, discount_margin
                FROM supplier_observations_legacy
                """,
                (DEFAULT_TENANT_ID,),
            )
            connection.execute("DROP TABLE supplier_observations_legacy")

        if not cls._has_column(connection, "supplier_quote_history", "tenant_id"):
            connection.execute(
                "ALTER TABLE supplier_quote_history RENAME TO supplier_quote_history_legacy"
            )
            connection.execute(
                """
                CREATE TABLE supplier_quote_history (
                    tenant_id TEXT NOT NULL,
                    vendor_id TEXT NOT NULL,
                    quote_id TEXT NOT NULL,
                    observed_on TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    average_unit_price TEXT NOT NULL,
                    freight_cost TEXT NOT NULL,
                    lead_time_days INTEGER NOT NULL,
                    payment_term_days INTEGER NOT NULL,
                    PRIMARY KEY (tenant_id, vendor_id, quote_id)
                )
                """
            )
            connection.execute(
                """
                INSERT INTO supplier_quote_history (
                    tenant_id, vendor_id, quote_id, observed_on, currency, average_unit_price,
                    freight_cost, lead_time_days, payment_term_days
                )
                SELECT ?, vendor_id, quote_id, observed_on, currency, average_unit_price,
                       freight_cost, lead_time_days, payment_term_days
                FROM supplier_quote_history_legacy
                """,
                (DEFAULT_TENANT_ID,),
            )
            connection.execute("DROP TABLE supplier_quote_history_legacy")

        if not cls._has_column(connection, "rfq_runs", "tenant_id"):
            connection.execute(
                "ALTER TABLE rfq_runs ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'local-demo'"
            )
        if not cls._has_column(connection, "rfq_approvals", "actor_subject"):
            connection.execute(
                "ALTER TABLE rfq_approvals "
                "ADD COLUMN actor_subject TEXT NOT NULL DEFAULT 'prototype-legacy'"
            )
            # Old approvals were made through the local buyer-ID field. Preserve
            # that known legacy actor instead of labeling every decision identically.
            connection.execute("UPDATE rfq_approvals SET actor_subject = buyer_id")
        approval_columns = (
            ("actor_tenant_id", "TEXT NOT NULL DEFAULT 'local-demo'"),
            ("authentication_method", "TEXT NOT NULL DEFAULT 'prototype'"),
        )
        for column_name, declaration in approval_columns:
            if not cls._has_column(connection, "rfq_approvals", column_name):
                connection.execute(
                    f"ALTER TABLE rfq_approvals ADD COLUMN {column_name} {declaration}"
                )

    def record_observation(
        self, observation: SupplierObservation, *, tenant_id: str = DEFAULT_TENANT_ID
    ) -> None:
        """Create or update one supplier performance event by quote identity."""

        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO supplier_observations (
                    tenant_id, vendor_id, quote_id, observed_on, currency, average_unit_price,
                    freight_cost, delivery_delay_days, responsiveness_score, discount_margin
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, vendor_id, quote_id) DO UPDATE SET
                    observed_on = excluded.observed_on,
                    currency = excluded.currency,
                    average_unit_price = excluded.average_unit_price,
                    freight_cost = excluded.freight_cost,
                    delivery_delay_days = excluded.delivery_delay_days,
                    responsiveness_score = excluded.responsiveness_score,
                    discount_margin = excluded.discount_margin
                """,
                (
                    tenant_id,
                    observation.vendor_id,
                    observation.quote_id,
                    observation.observed_on.isoformat(),
                    observation.currency,
                    str(observation.average_unit_price),
                    str(observation.freight_cost),
                    observation.delivery_delay_days,
                    str(observation.responsiveness_score),
                    str(observation.discount_margin),
                ),
            )

    def baseline_for(
        self,
        vendor_id: str,
        currency: str,
        *,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> SupplierBaseline | None:
        """Calculate a supplier performance baseline from outcome observations."""

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT average_unit_price, freight_cost, delivery_delay_days,
                       responsiveness_score, discount_margin
                FROM supplier_observations
                WHERE tenant_id = ? AND vendor_id = ? AND currency = ?
                ORDER BY observed_on ASC, quote_id ASC
                """,
                (tenant_id, vendor_id, currency.upper()),
            ).fetchall()

        if not rows:
            return None

        count = Decimal(len(rows))
        return SupplierBaseline(
            vendor_id=vendor_id,
            currency=currency.upper(),
            sample_size=len(rows),
            average_unit_price=sum(
                (Decimal(row["average_unit_price"]) for row in rows), Decimal("0")
            )
            / count,
            average_freight_cost=sum((Decimal(row["freight_cost"]) for row in rows), Decimal("0"))
            / count,
            average_delivery_delay_days=sum(
                (Decimal(row["delivery_delay_days"]) for row in rows), Decimal("0")
            )
            / count,
            average_responsiveness_score=sum(
                (Decimal(row["responsiveness_score"]) for row in rows), Decimal("0")
            )
            / count,
            average_discount_margin=sum(
                (Decimal(row["discount_margin"]) for row in rows), Decimal("0")
            )
            / count,
        )

    def record_quote_history(
        self,
        quote: NormalizedQuote,
        observed_on: date,
        *,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> None:
        """Store a normalized quote only after anomaly analysis completes."""

        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO supplier_quote_history (
                    tenant_id, vendor_id, quote_id, observed_on, currency, average_unit_price,
                    freight_cost, lead_time_days, payment_term_days
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, vendor_id, quote_id) DO UPDATE SET
                    observed_on = excluded.observed_on,
                    currency = excluded.currency,
                    average_unit_price = excluded.average_unit_price,
                    freight_cost = excluded.freight_cost,
                    lead_time_days = excluded.lead_time_days,
                    payment_term_days = excluded.payment_term_days
                """,
                (
                    tenant_id,
                    quote.vendor_id,
                    quote.quote_id,
                    observed_on.isoformat(),
                    quote.currency,
                    str(_average_quote_unit_price(quote)),
                    str(quote.freight_cost),
                    quote.lead_time_days,
                    quote.payment_terms.days,
                ),
            )

    def quote_baseline_for(
        self,
        vendor_id: str,
        currency: str,
        *,
        tenant_id: str = DEFAULT_TENANT_ID,
        as_of: date,
        lookback_days: int,
    ) -> SupplierQuoteBaseline | None:
        """Return a rolling baseline that excludes the current day's incoming bid."""

        window_start = as_of - timedelta(days=lookback_days)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT average_unit_price, freight_cost, lead_time_days, payment_term_days
                FROM supplier_quote_history
                WHERE tenant_id = ? AND vendor_id = ? AND currency = ?
                  AND observed_on >= ? AND observed_on < ?
                ORDER BY observed_on ASC, quote_id ASC
                """,
                (
                    tenant_id,
                    vendor_id,
                    currency.upper(),
                    window_start.isoformat(),
                    as_of.isoformat(),
                ),
            ).fetchall()

        if not rows:
            return None
        count = Decimal(len(rows))
        return SupplierQuoteBaseline(
            vendor_id=vendor_id,
            currency=currency.upper(),
            sample_size=len(rows),
            window_start=window_start,
            as_of=as_of,
            average_unit_price=sum(
                (Decimal(row["average_unit_price"]) for row in rows), Decimal("0")
            )
            / count,
            average_freight_cost=sum((Decimal(row["freight_cost"]) for row in rows), Decimal("0"))
            / count,
            average_lead_time_days=sum(
                (Decimal(row["lead_time_days"]) for row in rows), Decimal("0")
            )
            / count,
            average_payment_term_days=sum(
                (Decimal(row["payment_term_days"]) for row in rows), Decimal("0")
            )
            / count,
        )

    def create_rfq_run(
        self,
        *,
        run_id: str,
        requisition_id: str,
        tenant_id: str = DEFAULT_TENANT_ID,
        payload: Mapping[str, Any],
    ) -> RfqRun:
        """Create an intake run and its first immutable audit event."""

        now = _utc_now()
        payload_json = json.dumps(dict(payload), sort_keys=True)
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO rfq_runs (
                    run_id, requisition_id, tenant_id, state, version, created_at, updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    requisition_id,
                    tenant_id,
                    RfqRunState.INTAKE.value,
                    0,
                    now.isoformat(),
                    now.isoformat(),
                    payload_json,
                ),
            )
            self._insert_event(
                connection,
                run_id=run_id,
                sequence=1,
                occurred_at=now,
                event_type="run_created",
                from_state=None,
                to_state=RfqRunState.INTAKE,
                reason="RFQ run accepted for deterministic processing.",
                details={"requisition_id": requisition_id},
            )
        return self.load_rfq_run(run_id, tenant_id=tenant_id)

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
        """Advance only the deterministic pre-approval workflow states."""

        if to_state in {
            RfqRunState.AWAITING_APPROVAL,
            RfqRunState.APPROVED_DRAFT,
            RfqRunState.REJECTED,
        }:
            raise InvalidStateTransition(
                "approval states can only be changed through the ApprovalService control path"
            )
        return self._advance_rfq_run(
            run_id=run_id,
            to_state=to_state,
            event_type=event_type,
            reason=reason,
            tenant_id=tenant_id,
            details=details,
            payload_patch=payload_patch,
        )

    def request_buyer_approval(
        self,
        *,
        run_id: str,
        eligible_quote_ids: tuple[str, ...],
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> RfqRun:
        """Persist the buyer-review pause after approval policy finds eligible quotes."""

        if not eligible_quote_ids:
            raise ValueError("at least one eligible quote is required for buyer approval")
        return self._advance_rfq_run(
            run_id=run_id,
            to_state=RfqRunState.AWAITING_APPROVAL,
            event_type="buyer_approval_requested",
            reason="Within-budget options are ready for an authorized buyer decision.",
            tenant_id=tenant_id,
            details={
                "eligible_quote_ids": list(eligible_quote_ids),
                "execution_boundary": "draft_only",
            },
        )

    def _advance_rfq_run(
        self,
        *,
        run_id: str,
        to_state: RfqRunState,
        event_type: str,
        reason: str,
        tenant_id: str,
        details: Mapping[str, Any] | None = None,
        payload_patch: Mapping[str, Any] | None = None,
    ) -> RfqRun:
        """Atomically validate, advance, and audit one allowed state transition."""

        now = _utc_now()
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT state, version, payload_json FROM rfq_runs
                WHERE run_id = ? AND tenant_id = ?
                """,
                (run_id, tenant_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown RFQ run {run_id}")
            from_state = RfqRunState(row["state"])
            assert_transition(from_state, to_state)
            payload = json.loads(row["payload_json"])
            payload.update(payload_patch or {})
            version = int(row["version"]) + 1
            updated = connection.execute(
                """
                UPDATE rfq_runs
                SET state = ?, version = ?, updated_at = ?, payload_json = ?
                WHERE run_id = ? AND tenant_id = ? AND version = ?
                """,
                (
                    to_state.value,
                    version,
                    now.isoformat(),
                    json.dumps(payload, sort_keys=True),
                    run_id,
                    tenant_id,
                    version - 1,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("RFQ run changed concurrently; retry from persisted state")
            self._insert_event(
                connection,
                run_id=run_id,
                sequence=self._next_event_sequence(connection, run_id),
                occurred_at=now,
                event_type=event_type,
                from_state=from_state,
                to_state=to_state,
                reason=reason,
                details=dict(details or {}),
            )
        return self.load_rfq_run(run_id, tenant_id=tenant_id)

    def complete_buyer_approval(
        self,
        *,
        approval: ApprovalRecord,
        purchase_order_draft: PurchaseOrderDraft | None,
    ) -> RfqRun:
        """Atomically resolve an awaiting request and persist a non-binding draft.

        The service layer is responsible for buyer authorization and quote-budget
        validation. This method owns the all-or-nothing durable decision, state
        change, audit event, and optional PO-draft record.
        """

        if approval.decision is ApprovalDecision.APPROVE:
            if purchase_order_draft is None or approval.selected_quote_id is None:
                raise ValueError(
                    "an approved decision requires a selected quote and purchase-order draft"
                )
            if (
                purchase_order_draft.run_id != approval.run_id
                or purchase_order_draft.approved_by != approval.buyer_id
                or purchase_order_draft.source_quote_id != approval.selected_quote_id
                or purchase_order_draft.status != "draft_only"
            ):
                raise ValueError("purchase-order draft does not match the approved buyer decision")
        elif purchase_order_draft is not None or approval.selected_quote_id is not None:
            raise ValueError(
                "a rejected decision cannot select a quote or create a purchase-order draft"
            )

        target_state = (
            RfqRunState.APPROVED_DRAFT
            if approval.decision is ApprovalDecision.APPROVE
            else RfqRunState.REJECTED
        )
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT state, version, payload_json FROM rfq_runs
                WHERE run_id = ? AND tenant_id = ?
                """,
                (approval.run_id, approval.actor_tenant_id),
            ).fetchone()
            if row is None:
                raise PermissionError("identity is not authorized for this RFQ tenant")
            from_state = RfqRunState(row["state"])
            assert_transition(from_state, target_state)

            payload = json.loads(row["payload_json"])
            if purchase_order_draft is not None:
                comparison = QuoteComparison.model_validate(payload.get("comparison"))
                selected_score = next(
                    (
                        score
                        for score in comparison.ranked_quotes
                        if score.quote_id == approval.selected_quote_id
                    ),
                    None,
                )
                if selected_score is None or not selected_score.approval_ready:
                    raise ValueError("selected quote is not eligible for an approved draft")
                if (
                    purchase_order_draft.total_landed_cost
                    != selected_score.landed_cost.total_landed_cost
                ):
                    raise ValueError("purchase-order draft total does not match the approved quote")

            payload["approval"] = approval.model_dump(mode="json")
            if purchase_order_draft is not None:
                payload["purchase_order_draft"] = purchase_order_draft.model_dump(mode="json")

            connection.execute(
                """
                INSERT INTO rfq_approvals (
                    run_id, buyer_id, actor_subject, actor_tenant_id, authentication_method,
                    decision, selected_quote_id, note, decided_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval.run_id,
                    approval.buyer_id,
                    approval.actor_subject,
                    approval.actor_tenant_id,
                    approval.authentication_method.value,
                    approval.decision.value,
                    approval.selected_quote_id,
                    approval.note,
                    approval.decided_at.isoformat(),
                ),
            )
            if purchase_order_draft is not None:
                connection.execute(
                    """
                    INSERT INTO purchase_order_drafts (run_id, draft_id, created_at, payload_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        approval.run_id,
                        purchase_order_draft.draft_id,
                        purchase_order_draft.created_at.isoformat(),
                        purchase_order_draft.model_dump_json(),
                    ),
                )

            version = int(row["version"]) + 1
            updated = connection.execute(
                """
                UPDATE rfq_runs
                SET state = ?, version = ?, updated_at = ?, payload_json = ?
                WHERE run_id = ? AND tenant_id = ? AND version = ?
                """,
                (
                    target_state.value,
                    version,
                    approval.decided_at.isoformat(),
                    json.dumps(payload, sort_keys=True),
                    approval.run_id,
                    approval.actor_tenant_id,
                    version - 1,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("RFQ run changed concurrently; retry from persisted state")
            self._insert_event(
                connection,
                run_id=approval.run_id,
                sequence=self._next_event_sequence(connection, approval.run_id),
                occurred_at=approval.decided_at,
                event_type=(
                    "buyer_approved_draft" if purchase_order_draft is not None else "buyer_rejected"
                ),
                from_state=from_state,
                to_state=target_state,
                reason=(
                    "Authorized buyer approved a non-binding PO draft."
                    if purchase_order_draft is not None
                    else "Authorized buyer rejected the RFQ outcome."
                ),
                details={
                    "buyer_id": approval.buyer_id,
                    "actor_subject": approval.actor_subject,
                    "actor_tenant_id": approval.actor_tenant_id,
                    "authentication_method": approval.authentication_method.value,
                    "selected_quote_id": approval.selected_quote_id,
                    "execution_boundary": "draft_only",
                },
            )
        return self.load_rfq_run(approval.run_id, tenant_id=approval.actor_tenant_id)

    def approval_for(
        self, run_id: str, *, tenant_id: str = DEFAULT_TENANT_ID
    ) -> ApprovalRecord | None:
        """Return the immutable approval decision, when the buyer has decided."""

        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT approval.buyer_id, approval.actor_subject, approval.actor_tenant_id,
                       approval.authentication_method, approval.decision, approval.selected_quote_id,
                       approval.note, approval.decided_at
                FROM rfq_approvals AS approval
                JOIN rfq_runs AS run ON run.run_id = approval.run_id
                WHERE approval.run_id = ? AND run.tenant_id = ?
                """,
                (run_id, tenant_id),
            ).fetchone()
        if row is None:
            return None
        return ApprovalRecord(
            run_id=run_id,
            buyer_id=row["buyer_id"],
            actor_subject=row["actor_subject"],
            actor_tenant_id=row["actor_tenant_id"],
            authentication_method=AuthMode(row["authentication_method"]),
            decision=ApprovalDecision(row["decision"]),
            selected_quote_id=row["selected_quote_id"],
            note=row["note"],
            decided_at=datetime.fromisoformat(row["decided_at"]),
        )

    def purchase_order_draft_for(
        self, run_id: str, *, tenant_id: str = DEFAULT_TENANT_ID
    ) -> PurchaseOrderDraft | None:
        """Return the persisted non-binding PO payload, never an ERP order."""

        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT draft.payload_json
                FROM purchase_order_drafts AS draft
                JOIN rfq_runs AS run ON run.run_id = draft.run_id
                WHERE draft.run_id = ? AND run.tenant_id = ?
                """,
                (run_id, tenant_id),
            ).fetchone()
        return PurchaseOrderDraft.model_validate_json(row["payload_json"]) if row else None

    def record_rfq_event(
        self,
        *,
        run_id: str,
        event_type: str,
        reason: str,
        tenant_id: str = DEFAULT_TENANT_ID,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        """Append a non-transition event, such as a deterministic workflow failure."""

        with self._connection() as connection:
            run = connection.execute(
                "SELECT 1 FROM rfq_runs WHERE run_id = ? AND tenant_id = ?",
                (run_id, tenant_id),
            ).fetchone()
            if run is None:
                raise KeyError(f"unknown RFQ run {run_id}")
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS last_sequence FROM rfq_run_events WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            self._insert_event(
                connection,
                run_id=run_id,
                sequence=int(row["last_sequence"]) + 1,
                occurred_at=_utc_now(),
                event_type=event_type,
                from_state=None,
                to_state=None,
                reason=reason,
                details=dict(details or {}),
            )

    def load_rfq_run(self, run_id: str, *, tenant_id: str = DEFAULT_TENANT_ID) -> RfqRun:
        """Load current state without exposing mutable persistence objects."""

        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT run_id, requisition_id, tenant_id, state, version, created_at, updated_at
                FROM rfq_runs WHERE run_id = ? AND tenant_id = ?
                """,
                (run_id, tenant_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown RFQ run {run_id}")
        return RfqRun(
            run_id=row["run_id"],
            requisition_id=row["requisition_id"],
            tenant_id=row["tenant_id"],
            state=RfqRunState(row["state"]),
            version=row["version"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def audit_events_for(
        self, run_id: str, *, tenant_id: str = DEFAULT_TENANT_ID
    ) -> tuple[RfqAuditEvent, ...]:
        """Load the ordered event stream used to explain an RFQ decision."""

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT event.sequence, event.occurred_at, event.event_type, event.from_state,
                       event.to_state, event.reason, event.details_json
                FROM rfq_run_events AS event
                JOIN rfq_runs AS run ON run.run_id = event.run_id
                WHERE event.run_id = ? AND run.tenant_id = ?
                ORDER BY event.sequence ASC
                """,
                (run_id, tenant_id),
            ).fetchall()
        return tuple(
            RfqAuditEvent(
                run_id=run_id,
                sequence=row["sequence"],
                occurred_at=datetime.fromisoformat(row["occurred_at"]),
                event_type=row["event_type"],
                from_state=RfqRunState(row["from_state"]) if row["from_state"] else None,
                to_state=RfqRunState(row["to_state"]) if row["to_state"] else None,
                reason=row["reason"],
                details=json.loads(row["details_json"]),
            )
            for row in rows
        )

    def payload_for(self, run_id: str, *, tenant_id: str = DEFAULT_TENANT_ID) -> dict[str, Any]:
        """Return a JSON-safe workflow snapshot for resume/debug tooling."""

        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM rfq_runs WHERE run_id = ? AND tenant_id = ?",
                (run_id, tenant_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown RFQ run {run_id}")
        return json.loads(row["payload_json"])

    def observation_dates_for(
        self,
        vendor_id: str,
        currency: str,
        *,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> tuple[date, ...]:
        """Expose ordered outcome-history dates for future performance analyzers."""

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT observed_on FROM supplier_observations
                WHERE tenant_id = ? AND vendor_id = ? AND currency = ?
                ORDER BY observed_on ASC, quote_id ASC
                """,
                (tenant_id, vendor_id, currency.upper()),
            ).fetchall()
        return tuple(date.fromisoformat(row["observed_on"]) for row in rows)

    @staticmethod
    def _next_event_sequence(connection: sqlite3.Connection, run_id: str) -> int:
        row = connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS last_sequence FROM rfq_run_events WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return int(row["last_sequence"]) + 1

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        *,
        run_id: str,
        sequence: int,
        occurred_at: datetime,
        event_type: str,
        from_state: RfqRunState | None,
        to_state: RfqRunState | None,
        reason: str,
        details: Mapping[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO rfq_run_events (
                run_id, sequence, occurred_at, event_type, from_state, to_state, reason,
                details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                sequence,
                occurred_at.isoformat(),
                event_type,
                from_state.value if from_state else None,
                to_state.value if to_state else None,
                reason,
                json.dumps(dict(details), sort_keys=True),
            ),
        )
