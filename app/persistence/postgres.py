"""Postgres repository with tenant-scoped transactions and durable RFQ controls.

This backend is deliberately not auto-selected for local demos. Run its reviewed
migration before setting ``QUOTEHAWK_PERSISTENCE_BACKEND=postgres``. Every data
transaction sets a transaction-local tenant value used by the schema's row-level
security policies, and repeats the tenant predicate in application SQL.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.analysis.models import SupplierQuoteBaseline
from app.approval.models import ApprovalDecision, ApprovalRecord, PurchaseOrderDraft
from app.auth.models import DEFAULT_TENANT_ID, AuthMode
from app.domain.models import (
    NormalizedQuote,
    QuoteComparison,
    SupplierBaseline,
    SupplierObservation,
)
from app.negotiation.models import CounterOfferDraft
from app.persistence.repository import (
    ConcurrentRunUpdateError,
    IdempotencyConflictError,
    RfqRunCreation,
)
from app.workflow.models import RfqAuditEvent, RfqRun, RfqRunState
from app.workflow.state_machine import InvalidStateTransition, assert_transition

INITIAL_MIGRATION_VERSION = "0001_quotehawk_postgres"
_MIGRATIONS = (
    (
        INITIAL_MIGRATION_VERSION,
        Path(__file__).with_name("migrations") / "0001_quotehawk_postgres.sql",
    ),
    (
        "0002_workflow_resiliency",
        Path(__file__).with_name("migrations") / "0002_workflow_resiliency.sql",
    ),
)
ConnectionFactory = Callable[[str], Any]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _average_quote_unit_price(quote: NormalizedQuote) -> Decimal:
    quantity = sum((line.quoted_quantity for line in quote.lines), Decimal("0"))
    line_total = sum((line.quoted_quantity * line.unit_price for line in quote.lines), Decimal("0"))
    return line_total / quantity


def _json_object(value: object) -> dict[str, Any]:
    """Normalize psycopg JSONB output without ever accepting a non-object snapshot."""

    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        decoded = json.loads(value)
        if isinstance(decoded, dict):
            return decoded
    raise ValueError("persisted RFQ payload is not a JSON object")


def _migration_statements(path: Path | None = None) -> tuple[str, ...]:
    """Return a reviewed migration as individual PostgreSQL statements."""

    migration_path = path or _MIGRATIONS[0][1]
    migration = migration_path.read_text(encoding="utf-8")
    return tuple(statement.strip() for statement in migration.split(";") if statement.strip())


def _default_connection(dsn: str) -> psycopg.Connection[dict[str, Any]]:
    return psycopg.Connection[dict[str, Any]].connect(dsn, row_factory=dict_row)


class PostgresSupplierMemory:
    """Production-oriented repository preserving SQLite's observable semantics.

    The application role needs access only to the ``quotehawk`` schema. Each method
    binds a tenant to the transaction before querying, and the schema's RLS policy
    independently rejects rows for any other tenant. Migrations are explicit; this
    constructor never mutates schema during API startup.
    """

    def __init__(
        self,
        dsn: str,
        *,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        if not dsn.strip():
            raise ValueError("a non-empty Postgres DSN is required")
        self.dsn = dsn
        self._connection_factory = connection_factory or _default_connection

    @contextmanager
    def _tenant_connection(self, tenant_id: str) -> Generator[Any, None, None]:
        if not tenant_id.strip():
            raise ValueError("tenant ID must not be empty")
        connection = self._connection_factory(self.dsn)
        try:
            with connection.transaction():
                connection.execute(
                    "SELECT set_config('quotehawk.tenant_id', %s, true)", (tenant_id,)
                )
                yield connection
        finally:
            connection.close()

    @contextmanager
    def _schema_connection(self) -> Generator[Any, None, None]:
        connection = self._connection_factory(self.dsn)
        try:
            with connection.transaction():
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        """Apply the one-time, transactionally recorded Postgres schema migration."""

        with self._schema_connection() as connection:
            connection.execute("CREATE SCHEMA IF NOT EXISTS quotehawk")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS quotehawk.schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("quotehawk-schema",))
            for version, migration_path in _MIGRATIONS:
                applied = connection.execute(
                    "SELECT 1 FROM quotehawk.schema_migrations WHERE version = %s",
                    (version,),
                ).fetchone()
                if applied is not None:
                    continue
                for statement in _migration_statements(migration_path):
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO quotehawk.schema_migrations (version) VALUES (%s)",
                    (version,),
                )

    def record_observation(
        self, observation: SupplierObservation, *, tenant_id: str = DEFAULT_TENANT_ID
    ) -> None:
        """Create or update a tenant-owned supplier performance observation."""

        with self._tenant_connection(tenant_id) as connection:
            connection.execute(
                """
                INSERT INTO quotehawk.supplier_observations (
                    tenant_id, vendor_id, quote_id, observed_on, currency, average_unit_price,
                    freight_cost, delivery_delay_days, responsiveness_score, discount_margin
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, vendor_id, quote_id) DO UPDATE SET
                    observed_on = EXCLUDED.observed_on,
                    currency = EXCLUDED.currency,
                    average_unit_price = EXCLUDED.average_unit_price,
                    freight_cost = EXCLUDED.freight_cost,
                    delivery_delay_days = EXCLUDED.delivery_delay_days,
                    responsiveness_score = EXCLUDED.responsiveness_score,
                    discount_margin = EXCLUDED.discount_margin
                """,
                (
                    tenant_id,
                    observation.vendor_id,
                    observation.quote_id,
                    observation.observed_on,
                    observation.currency,
                    observation.average_unit_price,
                    observation.freight_cost,
                    observation.delivery_delay_days,
                    observation.responsiveness_score,
                    observation.discount_margin,
                ),
            )

    def baseline_for(
        self,
        vendor_id: str,
        currency: str,
        *,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> SupplierBaseline | None:
        """Calculate deterministic supplier performance only from this tenant's rows."""

        with self._tenant_connection(tenant_id) as connection:
            rows = connection.execute(
                """
                SELECT average_unit_price, freight_cost, delivery_delay_days,
                       responsiveness_score, discount_margin
                FROM quotehawk.supplier_observations
                WHERE tenant_id = %s AND vendor_id = %s AND currency = %s
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
            average_unit_price=sum((Decimal(row["average_unit_price"]) for row in rows), Decimal())
            / count,
            average_freight_cost=sum((Decimal(row["freight_cost"]) for row in rows), Decimal())
            / count,
            average_delivery_delay_days=sum(
                (Decimal(row["delivery_delay_days"]) for row in rows), Decimal()
            )
            / count,
            average_responsiveness_score=sum(
                (Decimal(row["responsiveness_score"]) for row in rows), Decimal()
            )
            / count,
            average_discount_margin=sum(
                (Decimal(row["discount_margin"]) for row in rows), Decimal()
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
        """Store a normalized quote after it has been analyzed against prior history."""

        with self._tenant_connection(tenant_id) as connection:
            connection.execute(
                """
                INSERT INTO quotehawk.supplier_quote_history (
                    tenant_id, vendor_id, quote_id, observed_on, currency, average_unit_price,
                    freight_cost, lead_time_days, payment_term_days
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, vendor_id, quote_id) DO UPDATE SET
                    observed_on = EXCLUDED.observed_on,
                    currency = EXCLUDED.currency,
                    average_unit_price = EXCLUDED.average_unit_price,
                    freight_cost = EXCLUDED.freight_cost,
                    lead_time_days = EXCLUDED.lead_time_days,
                    payment_term_days = EXCLUDED.payment_term_days
                """,
                (
                    tenant_id,
                    quote.vendor_id,
                    quote.quote_id,
                    observed_on,
                    quote.currency,
                    _average_quote_unit_price(quote),
                    quote.freight_cost,
                    quote.lead_time_days,
                    quote.effective_payment_days,
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
        """Return a prior-only rolling commercial baseline for the tenant/vendor."""

        window_start = as_of - timedelta(days=lookback_days)
        with self._tenant_connection(tenant_id) as connection:
            rows = connection.execute(
                """
                SELECT average_unit_price, freight_cost, lead_time_days, payment_term_days
                FROM quotehawk.supplier_quote_history
                WHERE tenant_id = %s AND vendor_id = %s AND currency = %s
                  AND observed_on >= %s AND observed_on < %s
                ORDER BY observed_on ASC, quote_id ASC
                """,
                (tenant_id, vendor_id, currency.upper(), window_start, as_of),
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
            average_unit_price=sum((Decimal(row["average_unit_price"]) for row in rows), Decimal())
            / count,
            average_freight_cost=sum((Decimal(row["freight_cost"]) for row in rows), Decimal())
            / count,
            average_lead_time_days=sum((Decimal(row["lead_time_days"]) for row in rows), Decimal())
            / count,
            average_payment_term_days=sum(
                (Decimal(row["payment_term_days"]) for row in rows), Decimal()
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
        """Create an intake run and first immutable audit event in one transaction."""

        creation = self.create_or_retrieve_rfq_run(
            run_id=run_id,
            requisition_id=requisition_id,
            tenant_id=tenant_id,
            payload=payload,
        )
        if not creation.created:
            raise ValueError(f"RFQ run {run_id} already exists")
        return creation.run

    def create_or_retrieve_rfq_run(
        self,
        *,
        run_id: str,
        requisition_id: str,
        tenant_id: str = DEFAULT_TENANT_ID,
        payload: Mapping[str, Any],
        idempotency_key: str | None = None,
        request_fingerprint: str | None = None,
    ) -> RfqRunCreation:
        """Atomically create a run or return the existing tenant/idempotency row."""

        if (idempotency_key is None) != (request_fingerprint is None):
            raise ValueError("idempotency key and request fingerprint must be supplied together")
        now = _utc_now()
        existing_run_id: str | None = None
        existing_fingerprint: str | None = None
        with self._tenant_connection(tenant_id) as connection:
            if idempotency_key is None:
                connection.execute(
                    """
                    INSERT INTO quotehawk.rfq_runs (
                        run_id, requisition_id, tenant_id, state, version, created_at, updated_at,
                        payload_json, idempotency_key, request_fingerprint
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        run_id,
                        requisition_id,
                        tenant_id,
                        RfqRunState.INTAKE.value,
                        0,
                        now,
                        now,
                        Jsonb(dict(payload)),
                        None,
                        None,
                    ),
                )
                created = True
            else:
                inserted = connection.execute(
                    """
                    INSERT INTO quotehawk.rfq_runs (
                        run_id, requisition_id, tenant_id, state, version, created_at, updated_at,
                        payload_json, idempotency_key, request_fingerprint
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (tenant_id, idempotency_key)
                    WHERE idempotency_key IS NOT NULL DO NOTHING
                    RETURNING run_id
                    """,
                    (
                        run_id,
                        requisition_id,
                        tenant_id,
                        RfqRunState.INTAKE.value,
                        0,
                        now,
                        now,
                        Jsonb(dict(payload)),
                        idempotency_key,
                        request_fingerprint,
                    ),
                ).fetchone()
                created = inserted is not None
                if not created:
                    existing = connection.execute(
                        """
                        SELECT run_id, request_fingerprint FROM quotehawk.rfq_runs
                        WHERE tenant_id = %s AND idempotency_key = %s
                        """,
                        (tenant_id, idempotency_key),
                    ).fetchone()
                    if existing is None:
                        raise RuntimeError("idempotency conflict could not be loaded")
                    existing_run_id = existing["run_id"]
                    existing_fingerprint = existing["request_fingerprint"]
                    if existing_fingerprint != request_fingerprint:
                        raise IdempotencyConflictError(
                            "idempotency key was already used for a different RFQ submission"
                        )
            if created:
                self._insert_event(
                    connection,
                    run_id=run_id,
                    tenant_id=tenant_id,
                    sequence=1,
                    occurred_at=now,
                    event_type="run_created",
                    from_state=None,
                    to_state=RfqRunState.INTAKE,
                    reason="RFQ run accepted for deterministic processing.",
                    details={"requisition_id": requisition_id},
                )
        if not created:
            return RfqRunCreation(
                run=self.load_rfq_run(existing_run_id or run_id, tenant_id=tenant_id),
                created=False,
                request_fingerprint=existing_fingerprint,
            )
        return RfqRunCreation(
            run=self.load_rfq_run(run_id, tenant_id=tenant_id),
            created=True,
            request_fingerprint=request_fingerprint,
        )

    def readiness_check(self) -> None:
        """Verify reachable Postgres schema versions without reading tenant data."""

        with self._schema_connection() as connection:
            rows = connection.execute("SELECT version FROM quotehawk.schema_migrations").fetchall()
        applied = {row["version"] for row in rows}
        required = {version for version, _ in _MIGRATIONS}
        if not required.issubset(applied):
            raise RuntimeError("Postgres schema migration is incomplete")

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
        """Advance only deterministic pre-approval states within one tenant."""

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
        """Persist a buyer-review pause after deterministic eligibility checks."""

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
        """Lock, validate, compare-and-swap, and audit one state transition."""

        now = _utc_now()
        with self._tenant_connection(tenant_id) as connection:
            row = connection.execute(
                """
                SELECT state, version, payload_json
                FROM quotehawk.rfq_runs
                WHERE run_id = %s AND tenant_id = %s
                FOR UPDATE
                """,
                (run_id, tenant_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown RFQ run {run_id}")
            from_state = RfqRunState(row["state"])
            assert_transition(from_state, to_state)
            payload = _json_object(row["payload_json"])
            payload.update(payload_patch or {})
            version = int(row["version"]) + 1
            updated = connection.execute(
                """
                UPDATE quotehawk.rfq_runs
                SET state = %s, version = %s, updated_at = %s, payload_json = %s
                WHERE run_id = %s AND tenant_id = %s AND version = %s
                """,
                (
                    to_state.value,
                    version,
                    now,
                    Jsonb(payload),
                    run_id,
                    tenant_id,
                    version - 1,
                ),
            )
            if updated.rowcount != 1:
                raise ConcurrentRunUpdateError(
                    "RFQ run changed concurrently; retry from persisted state"
                )
            self._insert_event(
                connection,
                run_id=run_id,
                tenant_id=tenant_id,
                sequence=self._next_event_sequence(connection, run_id, tenant_id),
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
        """Atomically resolve an awaiting gate and optional non-binding draft."""

        if approval.decision in {ApprovalDecision.APPROVE, ApprovalDecision.OVERRIDE}:
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
            if approval.decision in {ApprovalDecision.APPROVE, ApprovalDecision.OVERRIDE}
            else RfqRunState.REJECTED
        )
        with self._tenant_connection(approval.actor_tenant_id) as connection:
            row = connection.execute(
                """
                SELECT state, version, payload_json
                FROM quotehawk.rfq_runs
                WHERE run_id = %s AND tenant_id = %s
                FOR UPDATE
                """,
                (approval.run_id, approval.actor_tenant_id),
            ).fetchone()
            if row is None:
                raise PermissionError("identity is not authorized for this RFQ tenant")
            from_state = RfqRunState(row["state"])
            assert_transition(from_state, target_state)
            payload = _json_object(row["payload_json"])
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
                INSERT INTO quotehawk.rfq_approvals (
                    run_id, tenant_id, buyer_id, actor_subject, actor_tenant_id,
                    authentication_method, decision, selected_quote_id, note, decided_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    approval.run_id,
                    approval.actor_tenant_id,
                    approval.buyer_id,
                    approval.actor_subject,
                    approval.actor_tenant_id,
                    approval.authentication_method.value,
                    approval.decision.value,
                    approval.selected_quote_id,
                    approval.note,
                    approval.decided_at,
                ),
            )
            if purchase_order_draft is not None:
                connection.execute(
                    """
                    INSERT INTO quotehawk.purchase_order_drafts (
                        run_id, tenant_id, draft_id, created_at, payload_json
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        approval.run_id,
                        approval.actor_tenant_id,
                        purchase_order_draft.draft_id,
                        purchase_order_draft.created_at,
                        Jsonb(purchase_order_draft.model_dump(mode="json")),
                    ),
                )
            version = int(row["version"]) + 1
            updated = connection.execute(
                """
                UPDATE quotehawk.rfq_runs
                SET state = %s, version = %s, updated_at = %s, payload_json = %s
                WHERE run_id = %s AND tenant_id = %s AND version = %s
                """,
                (
                    target_state.value,
                    version,
                    approval.decided_at,
                    Jsonb(payload),
                    approval.run_id,
                    approval.actor_tenant_id,
                    version - 1,
                ),
            )
            if updated.rowcount != 1:
                raise ConcurrentRunUpdateError(
                    "RFQ run changed concurrently; retry from persisted state"
                )
            self._insert_event(
                connection,
                run_id=approval.run_id,
                tenant_id=approval.actor_tenant_id,
                sequence=self._next_event_sequence(
                    connection, approval.run_id, approval.actor_tenant_id
                ),
                occurred_at=approval.decided_at,
                event_type=(
                    "buyer_overrode_draft"
                    if approval.decision is ApprovalDecision.OVERRIDE
                    else "buyer_approved_draft"
                    if purchase_order_draft is not None
                    else "buyer_rejected"
                ),
                from_state=from_state,
                to_state=target_state,
                reason=(
                    "Authorized buyer recorded a documented override for an eligible non-binding PO draft."
                    if approval.decision is ApprovalDecision.OVERRIDE
                    else "Authorized buyer approved a non-binding PO draft."
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
        """Return an immutable approval decision visible to one tenant."""

        with self._tenant_connection(tenant_id) as connection:
            row = connection.execute(
                """
                SELECT buyer_id, actor_subject, actor_tenant_id, authentication_method,
                       decision, selected_quote_id, note, decided_at
                FROM quotehawk.rfq_approvals
                WHERE run_id = %s AND tenant_id = %s
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
            decided_at=row["decided_at"],
        )

    def purchase_order_draft_for(
        self, run_id: str, *, tenant_id: str = DEFAULT_TENANT_ID
    ) -> PurchaseOrderDraft | None:
        """Return a non-binding PO payload visible to one tenant."""

        with self._tenant_connection(tenant_id) as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM quotehawk.purchase_order_drafts
                WHERE run_id = %s AND tenant_id = %s
                """,
                (run_id, tenant_id),
            ).fetchone()
        return PurchaseOrderDraft.model_validate(_json_object(row["payload_json"])) if row else None

    def record_rfq_event(
        self,
        *,
        run_id: str,
        event_type: str,
        reason: str,
        tenant_id: str = DEFAULT_TENANT_ID,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        """Append a tenant-owned non-transition event under the run row lock."""

        with self._tenant_connection(tenant_id) as connection:
            run = connection.execute(
                """
                SELECT 1 FROM quotehawk.rfq_runs
                WHERE run_id = %s AND tenant_id = %s
                FOR UPDATE
                """,
                (run_id, tenant_id),
            ).fetchone()
            if run is None:
                raise KeyError(f"unknown RFQ run {run_id}")
            self._insert_event(
                connection,
                run_id=run_id,
                tenant_id=tenant_id,
                sequence=self._next_event_sequence(connection, run_id, tenant_id),
                occurred_at=_utc_now(),
                event_type=event_type,
                from_state=None,
                to_state=None,
                reason=reason,
                details=dict(details or {}),
            )

    def store_counter_offer_draft(
        self,
        *,
        draft: CounterOfferDraft,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> None:
        """Persist one simulated, buyer-reviewable thread and its safe audit fact."""

        now = _utc_now()
        with self._tenant_connection(tenant_id) as connection:
            row = connection.execute(
                """
                SELECT state, version, payload_json FROM quotehawk.rfq_runs
                WHERE run_id = %s AND tenant_id = %s
                FOR UPDATE
                """,
                (draft.run_id, tenant_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown RFQ run {draft.run_id}")
            if RfqRunState(row["state"]) is not RfqRunState.NEGOTIATION_OR_READY:
                raise InvalidStateTransition(
                    "counter-offer drafts can only be stored before buyer approval opens"
                )
            payload = _json_object(row["payload_json"])
            stored_drafts = payload.get("counter_offer_drafts", {})
            if not isinstance(stored_drafts, dict):
                raise ValueError("RFQ run contains invalid counter-offer draft storage")
            stored_drafts[draft.vendor_id] = draft.model_dump(mode="json")
            payload["counter_offer_drafts"] = stored_drafts
            version = int(row["version"]) + 1
            updated = connection.execute(
                """
                UPDATE quotehawk.rfq_runs
                SET version = %s, updated_at = %s, payload_json = %s
                WHERE run_id = %s AND tenant_id = %s AND version = %s
                """,
                (
                    version,
                    now,
                    Jsonb(payload),
                    draft.run_id,
                    tenant_id,
                    version - 1,
                ),
            )
            if updated.rowcount != 1:
                raise ConcurrentRunUpdateError(
                    "RFQ run changed concurrently; retry from persisted state"
                )
            self._insert_event(
                connection,
                run_id=draft.run_id,
                tenant_id=tenant_id,
                sequence=self._next_event_sequence(connection, draft.run_id, tenant_id),
                occurred_at=now,
                event_type="counter_offer_drafted",
                from_state=None,
                to_state=None,
                reason="A draft-only simulated negotiation thread was stored for buyer review.",
                details={
                    "vendor_id": draft.vendor_id,
                    "provider": draft.provider.value,
                    "simulated_rounds": len(draft.thread) // 2,
                    "execution_boundary": "draft_only",
                },
            )

    def load_rfq_run(self, run_id: str, *, tenant_id: str = DEFAULT_TENANT_ID) -> RfqRun:
        """Load a tenant-owned run without exposing mutable persistence objects."""

        with self._tenant_connection(tenant_id) as connection:
            row = connection.execute(
                """
                SELECT run_id, requisition_id, tenant_id, state, version, created_at, updated_at
                FROM quotehawk.rfq_runs
                WHERE run_id = %s AND tenant_id = %s
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
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def audit_events_for(
        self, run_id: str, *, tenant_id: str = DEFAULT_TENANT_ID
    ) -> tuple[RfqAuditEvent, ...]:
        """Load the ordered tenant-owned audit stream."""

        with self._tenant_connection(tenant_id) as connection:
            rows = connection.execute(
                """
                SELECT sequence, occurred_at, event_type, from_state, to_state, reason, details_json
                FROM quotehawk.rfq_run_events
                WHERE run_id = %s AND tenant_id = %s
                ORDER BY sequence ASC
                """,
                (run_id, tenant_id),
            ).fetchall()
        return tuple(
            RfqAuditEvent(
                run_id=run_id,
                sequence=row["sequence"],
                occurred_at=row["occurred_at"],
                event_type=row["event_type"],
                from_state=RfqRunState(row["from_state"]) if row["from_state"] else None,
                to_state=RfqRunState(row["to_state"]) if row["to_state"] else None,
                reason=row["reason"],
                details=_json_object(row["details_json"]),
            )
            for row in rows
        )

    def payload_for(self, run_id: str, *, tenant_id: str = DEFAULT_TENANT_ID) -> dict[str, Any]:
        """Return the JSON-safe snapshot visible only to one tenant."""

        with self._tenant_connection(tenant_id) as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM quotehawk.rfq_runs
                WHERE run_id = %s AND tenant_id = %s
                """,
                (run_id, tenant_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown RFQ run {run_id}")
        return _json_object(row["payload_json"])

    def observation_dates_for(
        self,
        vendor_id: str,
        currency: str,
        *,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> tuple[date, ...]:
        """Expose ordered outcome-history dates for future performance analyzers."""

        with self._tenant_connection(tenant_id) as connection:
            rows = connection.execute(
                """
                SELECT observed_on FROM quotehawk.supplier_observations
                WHERE tenant_id = %s AND vendor_id = %s AND currency = %s
                ORDER BY observed_on ASC, quote_id ASC
                """,
                (tenant_id, vendor_id, currency.upper()),
            ).fetchall()
        return tuple(row["observed_on"] for row in rows)

    @staticmethod
    def _next_event_sequence(connection: Any, run_id: str, tenant_id: str) -> int:
        row = connection.execute(
            """
            SELECT COALESCE(MAX(sequence), 0) AS last_sequence
            FROM quotehawk.rfq_run_events
            WHERE run_id = %s AND tenant_id = %s
            """,
            (run_id, tenant_id),
        ).fetchone()
        return int(row["last_sequence"]) + 1

    @staticmethod
    def _insert_event(
        connection: Any,
        *,
        run_id: str,
        tenant_id: str,
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
            INSERT INTO quotehawk.rfq_run_events (
                tenant_id, run_id, sequence, occurred_at, event_type, from_state, to_state, reason,
                details_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                tenant_id,
                run_id,
                sequence,
                occurred_at,
                event_type,
                from_state.value if from_state else None,
                to_state.value if to_state else None,
                reason,
                Jsonb(dict(details)),
            ),
        )


def _main() -> None:
    parser = argparse.ArgumentParser(description="Apply the reviewed QuoteHawk Postgres schema")
    parser.add_argument(
        "--dsn", required=True, help="Postgres DSN; never place it in source control"
    )
    args = parser.parse_args()
    PostgresSupplierMemory(args.dsn).initialize()


if __name__ == "__main__":
    _main()
