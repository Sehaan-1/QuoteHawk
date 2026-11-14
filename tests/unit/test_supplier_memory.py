"""Supplier memory must persist exact, explainable historical baselines."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.domain.models import SupplierObservation
from app.persistence.sqlite import SqliteSupplierMemory


def _observation(
    quote_id: str, observed_on: date, freight_cost: str, unit_price: str
) -> SupplierObservation:
    return SupplierObservation(
        vendor_id="vendor-alpha",
        quote_id=quote_id,
        observed_on=observed_on,
        currency="USD",
        average_unit_price=Decimal(unit_price),
        freight_cost=Decimal(freight_cost),
        delivery_delay_days=2,
        responsiveness_score=Decimal("0.8"),
        discount_margin=Decimal("0.1"),
    )


def test_sqlite_memory_survives_repository_recreation(tmp_path) -> None:
    database_path = tmp_path / "quotehawk.sqlite3"
    memory = SqliteSupplierMemory(database_path)
    memory.record_observation(_observation("HIST-1", date(2026, 1, 1), "100", "10"))
    memory.record_observation(_observation("HIST-2", date(2026, 2, 1), "120", "12"))

    reloaded_memory = SqliteSupplierMemory(database_path)
    baseline = reloaded_memory.baseline_for("vendor-alpha", "USD")

    assert baseline is not None
    assert baseline.sample_size == 2
    assert baseline.average_freight_cost == Decimal("110")
    assert baseline.average_unit_price == Decimal("11")
    assert reloaded_memory.observation_dates_for("vendor-alpha", "USD") == (
        date(2026, 1, 1),
        date(2026, 2, 1),
    )


def test_memory_upsert_avoids_duplicate_supplier_history(tmp_path) -> None:
    memory = SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3")
    memory.record_observation(_observation("HIST-1", date(2026, 1, 1), "100", "10"))
    memory.record_observation(_observation("HIST-1", date(2026, 1, 2), "130", "11"))

    baseline = memory.baseline_for("vendor-alpha", "USD")

    assert baseline is not None
    assert baseline.sample_size == 1
    assert baseline.average_freight_cost == Decimal("130")
