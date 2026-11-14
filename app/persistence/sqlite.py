"""SQLite-backed supplier memory for the local QuoteHawk prototype."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.domain.models import SupplierBaseline, SupplierObservation


class SqliteSupplierMemory:
    """Persist supplier observations without losing decimal precision.

    Monetary data is written as canonical decimal text rather than SQLite REAL so
    a six-month baseline is reproducible exactly. The class opens short-lived
    connections, which keeps it safe for FastAPI's later threaded request model.
    """

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        self.initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
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

    def record_observation(self, observation: SupplierObservation) -> None:
        """Create or update one supplier memory event by stable quote identity."""

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
        """Calculate a cross-run supplier baseline from local historical data."""

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

    def observation_dates_for(self, vendor_id: str, currency: str) -> tuple[date, ...]:
        """Expose ordered history dates for future rolling-window analyzers."""

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
