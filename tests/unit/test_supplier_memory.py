"""Supplier memory must persist exact, explainable historical baselines."""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal

import pytest

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


def test_supplier_memory_isolated_by_tenant_even_for_matching_vendor_and_quote_ids(
    tmp_path,
) -> None:
    memory = SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3")
    memory.record_observation(
        _observation("SHARED-QUOTE", date(2026, 1, 1), "100", "10"),
        tenant_id="tenant-acme",
    )
    memory.record_observation(
        _observation("SHARED-QUOTE", date(2026, 1, 1), "220", "22"),
        tenant_id="tenant-globex",
    )

    acme = memory.baseline_for("vendor-alpha", "USD", tenant_id="tenant-acme")
    globex = memory.baseline_for("vendor-alpha", "USD", tenant_id="tenant-globex")

    assert acme is not None and acme.average_freight_cost == Decimal("100")
    assert globex is not None and globex.average_freight_cost == Decimal("220")


def test_sqlite_run_reads_and_audit_streams_are_scoped_by_tenant(tmp_path) -> None:
    memory = SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3")
    memory.create_rfq_run(
        run_id="acme-run",
        requisition_id="PR-ACME",
        tenant_id="tenant-acme",
        payload={},
    )

    assert memory.load_rfq_run("acme-run", tenant_id="tenant-acme").tenant_id == "tenant-acme"
    assert len(memory.audit_events_for("acme-run", tenant_id="tenant-acme")) == 1
    assert memory.audit_events_for("acme-run", tenant_id="tenant-globex") == ()
    with pytest.raises(KeyError):
        memory.load_rfq_run("acme-run", tenant_id="tenant-globex")
    with pytest.raises(KeyError):
        memory.payload_for("acme-run", tenant_id="tenant-globex")


def test_sqlite_migrates_legacy_commercial_history_into_the_local_demo_tenant(tmp_path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE supplier_observations (
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
            INSERT INTO supplier_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("vendor-alpha", "LEGACY-1", "2026-01-01", "USD", "10", "100", 2, "0.8", "0.1"),
        )
        connection.execute(
            """
            CREATE TABLE supplier_quote_history (
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

    memory = SqliteSupplierMemory(database_path)

    assert memory.baseline_for("vendor-alpha", "USD") is not None
    assert memory.baseline_for("vendor-alpha", "USD", tenant_id="tenant-acme") is None


def test_sqlite_migrates_legacy_run_and_approval_actor_fields(tmp_path) -> None:
    database_path = tmp_path / "legacy-run.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE rfq_runs (
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
            INSERT INTO rfq_runs VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-run",
                "PR-LEGACY",
                "rejected",
                5,
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                "{}",
            ),
        )
        connection.execute(
            """
            CREATE TABLE rfq_approvals (
                run_id TEXT PRIMARY KEY,
                buyer_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                selected_quote_id TEXT,
                note TEXT,
                decided_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO rfq_approvals VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-run",
                "legacy-buyer",
                "reject",
                None,
                "legacy decision",
                "2026-01-01T00:00:00+00:00",
            ),
        )

    memory = SqliteSupplierMemory(database_path)
    approval = memory.approval_for("legacy-run")

    assert memory.load_rfq_run("legacy-run").tenant_id == "local-demo"
    assert approval is not None
    assert approval.actor_subject == "legacy-buyer"
    assert approval.actor_tenant_id == "local-demo"
    assert approval.authentication_method.value == "prototype"
