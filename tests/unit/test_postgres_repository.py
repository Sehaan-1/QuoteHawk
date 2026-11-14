"""Offline contracts for the reviewed Postgres persistence path."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.persistence.factory import repository_from_environment
from app.persistence.postgres import (
    INITIAL_MIGRATION_VERSION,
    PostgresSupplierMemory,
    _migration_statements,
)
from app.persistence.sqlite import SqliteSupplierMemory


class _Cursor:
    def __init__(
        self, *, row: dict[str, Any] | None = None, rows: list[dict[str, Any]] | None = None
    ):
        self._row = row
        self._rows = rows or []
        self.rowcount = 1

    def fetchone(self) -> dict[str, Any] | None:
        return self._row

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...] | None]] = []
        self.closed = False
        self.migration_applied = False
        self.run_row = {
            "run_id": "tenant-run",
            "requisition_id": "PR-POSTGRES",
            "tenant_id": "tenant-acme",
            "state": "intake",
            "version": 0,
            "created_at": datetime(2026, 11, 14, tzinfo=UTC),
            "updated_at": datetime(2026, 11, 14, tzinfo=UTC),
        }

    def transaction(self):  # type: ignore[no-untyped-def]
        return nullcontext()

    def execute(self, statement: str, parameters: tuple[object, ...] | None = None) -> _Cursor:
        self.calls.append((statement, parameters))
        if "FROM quotehawk.schema_migrations" in statement:
            return _Cursor(row={"present": 1} if self.migration_applied else None)
        if statement.startswith("INSERT INTO quotehawk.schema_migrations"):
            self.migration_applied = True
        if "SELECT run_id, requisition_id, tenant_id" in statement:
            return _Cursor(row=self.run_row)
        return _Cursor()

    def close(self) -> None:
        self.closed = True


class _ConnectionFactory:
    def __init__(self) -> None:
        self.connections: list[_RecordingConnection] = []

    def __call__(self, _dsn: str) -> _RecordingConnection:
        connection = _RecordingConnection()
        if self.connections:
            connection.migration_applied = self.connections[-1].migration_applied
        self.connections.append(connection)
        return connection


def test_postgres_migration_defines_tenant_foreign_keys_and_row_level_security() -> None:
    migration = Path("app/persistence/migrations/0001_quotehawk_postgres.sql").read_text()

    assert "PRIMARY KEY (tenant_id, run_id, sequence)" in migration
    assert "CHECK (tenant_id = actor_tenant_id)" in migration
    assert "FOREIGN KEY (run_id, tenant_id)" in migration
    assert "FORCE ROW LEVEL SECURITY" in migration
    assert "current_setting('quotehawk.tenant_id', true)" in migration
    assert "NUMERIC NOT NULL" in migration


def test_postgres_constructor_never_connects_or_migrates_implicitly() -> None:
    factory = _ConnectionFactory()

    repository = PostgresSupplierMemory(
        "postgresql://example.invalid/quotehawk", connection_factory=factory
    )

    assert repository.dsn == "postgresql://example.invalid/quotehawk"
    assert factory.connections == []


def test_postgres_initialize_records_the_schema_version_only_once() -> None:
    factory = _ConnectionFactory()
    repository = PostgresSupplierMemory(
        "postgresql://offline/quotehawk", connection_factory=factory
    )

    repository.initialize()
    repository.initialize()

    first, second = factory.connections
    first_statements = [statement for statement, _ in first.calls]
    second_statements = [statement for statement, _ in second.calls]
    assert any("CREATE TABLE IF NOT EXISTS quotehawk.rfq_runs" in item for item in first_statements)
    assert any("CREATE POLICY rfq_runs_tenant_policy" in item for item in first_statements)
    assert any(
        parameters == (INITIAL_MIGRATION_VERSION,)
        for statement, parameters in first.calls
        if "INSERT INTO quotehawk.schema_migrations" in statement
    )
    assert not any("CREATE POLICY" in item for item in second_statements)
    assert first.closed and second.closed


def test_postgres_load_binds_the_tenant_before_a_tenant_predicate_query() -> None:
    factory = _ConnectionFactory()
    repository = PostgresSupplierMemory(
        "postgresql://offline/quotehawk", connection_factory=factory
    )

    run = repository.load_rfq_run("tenant-run", tenant_id="tenant-acme")

    connection = factory.connections[0]
    assert run.tenant_id == "tenant-acme"
    assert connection.calls[0] == (
        "SELECT set_config('quotehawk.tenant_id', %s, true)",
        ("tenant-acme",),
    )
    query, parameters = connection.calls[1]
    assert "WHERE run_id = %s AND tenant_id = %s" in query
    assert parameters == ("tenant-run", "tenant-acme")
    assert connection.closed


def test_persistence_factory_requires_a_dsn_and_preserves_explicit_sqlite_mode(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("QUOTEHAWK_PERSISTENCE_BACKEND", "postgres")
    monkeypatch.delenv("QUOTEHAWK_POSTGRES_DSN", raising=False)
    with pytest.raises(ValueError, match="QUOTEHAWK_POSTGRES_DSN"):
        repository_from_environment()

    monkeypatch.setenv("QUOTEHAWK_POSTGRES_DSN", "postgresql://offline/quotehawk")
    assert isinstance(repository_from_environment(), PostgresSupplierMemory)

    monkeypatch.setenv("QUOTEHAWK_PERSISTENCE_BACKEND", "sqlite")
    monkeypatch.setenv("QUOTEHAWK_DATABASE_PATH", str(tmp_path / "quotehawk.sqlite3"))
    assert isinstance(repository_from_environment(), SqliteSupplierMemory)


def test_migration_parser_retains_the_reviewed_policy_statements() -> None:
    statements = _migration_statements()

    assert statements[0] == "CREATE SCHEMA IF NOT EXISTS quotehawk"
    assert any(
        "CREATE POLICY purchase_order_drafts_tenant_policy" in statement for statement in statements
    )
