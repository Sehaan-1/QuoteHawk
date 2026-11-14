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
from app.domain.models import NormalizedQuote, SupplierBaseline, SupplierObservation
from app.workflow.models import RfqAuditEvent, RfqRun, RfqRunState
from app.workflow.state_machine import assert_transition


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
                    vendor_id TEXT NOT NULL,
                    quote_id TEXT NOT NULL,
                    observed_on TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    average_unit_price TEXT NOT NULL,
                    freight_cost TEXT NOT NULL,
                    delivery_delay_days INTEGER NOT NULL,
                    responsiveness_score TEXT NOT NULL,
                    discount_margin TEXT NOT NULL,
                    PRIMARY KEY (vendor_id, quote_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_supplier_observations_vendor_currency
                ON supplier_observations (vendor_id, currency, observed_on)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS supplier_quote_history (
                    vendor_id TEXT NOT NULL,
                    quote_id TEXT NOT NULL,
                    observed_on TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    average_unit_price TEXT NOT NULL,
                    freight_cost TEXT NOT NULL,
                    lead_time_days INTEGER NOT NULL,
                    payment_term_days INTEGER NOT NULL,
                    PRIMARY KEY (vendor_id, quote_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_supplier_quote_history_window
                ON supplier_quote_history (vendor_id, currency, observed_on)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS rfq_runs (
                    run_id TEXT PRIMARY KEY,
                    requisition_id TEXT NOT NULL,
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

    def record_observation(self, observation: SupplierObservation) -> None:
        """Create or update one supplier performance event by quote identity."""

        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO supplier_observations (
                    vendor_id, quote_id, observed_on, currency, average_unit_price,
                    freight_cost, delivery_delay_days, responsiveness_score,
                    discount_margin
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(vendor_id, quote_id) DO UPDATE SET
                    observed_on = excluded.observed_on,
                    currency = excluded.currency,
                    average_unit_price = excluded.average_unit_price,
                    freight_cost = excluded.freight_cost,
                    delivery_delay_days = excluded.delivery_delay_days,
                    responsiveness_score = excluded.responsiveness_score,
                    discount_margin = excluded.discount_margin
                """,
                (
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

    def baseline_for(self, vendor_id: str, currency: str) -> SupplierBaseline | None:
        """Calculate a supplier performance baseline from outcome observations."""

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT average_unit_price, freight_cost, delivery_delay_days,
                       responsiveness_score, discount_margin
                FROM supplier_observations
                WHERE vendor_id = ? AND currency = ?
                ORDER BY observed_on ASC, quote_id ASC
                """,
                (vendor_id, currency.upper()),
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

    def record_quote_history(self, quote: NormalizedQuote, observed_on: date) -> None:
        """Store a normalized quote only after anomaly analysis completes."""

        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO supplier_quote_history (
                    vendor_id, quote_id, observed_on, currency, average_unit_price,
                    freight_cost, lead_time_days, payment_term_days
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(vendor_id, quote_id) DO UPDATE SET
                    observed_on = excluded.observed_on,
                    currency = excluded.currency,
                    average_unit_price = excluded.average_unit_price,
                    freight_cost = excluded.freight_cost,
                    lead_time_days = excluded.lead_time_days,
                    payment_term_days = excluded.payment_term_days
                """,
                (
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
                WHERE vendor_id = ? AND currency = ?
                  AND observed_on >= ? AND observed_on < ?
                ORDER BY observed_on ASC, quote_id ASC
                """,
                (vendor_id, currency.upper(), window_start.isoformat(), as_of.isoformat()),
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
        payload: Mapping[str, Any],
    ) -> RfqRun:
        """Create an intake run and its first immutable audit event."""

        now = _utc_now()
        payload_json = json.dumps(dict(payload), sort_keys=True)
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO rfq_runs (
                    run_id, requisition_id, state, version, created_at, updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    requisition_id,
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
        return self.load_rfq_run(run_id)

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
        """Atomically validate, advance, and audit one allowed state transition."""

        now = _utc_now()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT state, version, payload_json FROM rfq_runs WHERE run_id = ?", (run_id,)
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
                WHERE run_id = ? AND version = ?
                """,
                (
                    to_state.value,
                    version,
                    now.isoformat(),
                    json.dumps(payload, sort_keys=True),
                    run_id,
                    version - 1,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("RFQ run changed concurrently; retry from persisted state")
            self._insert_event(
                connection,
                run_id=run_id,
                sequence=version + 1,
                occurred_at=now,
                event_type=event_type,
                from_state=from_state,
                to_state=to_state,
                reason=reason,
                details=dict(details or {}),
            )
        return self.load_rfq_run(run_id)

    def record_rfq_event(
        self,
        *,
        run_id: str,
        event_type: str,
        reason: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        """Append a non-transition event, such as a deterministic workflow failure."""

        with self._connection() as connection:
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

    def load_rfq_run(self, run_id: str) -> RfqRun:
        """Load current state without exposing mutable persistence objects."""

        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT run_id, requisition_id, state, version, created_at, updated_at
                FROM rfq_runs WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown RFQ run {run_id}")
        return RfqRun(
            run_id=row["run_id"],
            requisition_id=row["requisition_id"],
            state=RfqRunState(row["state"]),
            version=row["version"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def audit_events_for(self, run_id: str) -> tuple[RfqAuditEvent, ...]:
        """Load the ordered event stream used to explain an RFQ decision."""

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT sequence, occurred_at, event_type, from_state, to_state, reason, details_json
                FROM rfq_run_events WHERE run_id = ? ORDER BY sequence ASC
                """,
                (run_id,),
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

    def payload_for(self, run_id: str) -> dict[str, Any]:
        """Return a JSON-safe workflow snapshot for resume/debug tooling."""

        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM rfq_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown RFQ run {run_id}")
        return json.loads(row["payload_json"])

    def observation_dates_for(self, vendor_id: str, currency: str) -> tuple[date, ...]:
        """Expose ordered outcome-history dates for future performance analyzers."""

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT observed_on FROM supplier_observations
                WHERE vendor_id = ? AND currency = ?
                ORDER BY observed_on ASC, quote_id ASC
                """,
                (vendor_id, currency.upper()),
            ).fetchall()
        return tuple(date.fromisoformat(row["observed_on"]) for row in rows)

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
