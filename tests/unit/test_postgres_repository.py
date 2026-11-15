"""Offline contracts for the reviewed Postgres persistence path."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from psycopg_pool import ConnectionPool

from app.persistence.factory import repository_from_environment
from app.persistence.postgres import _MIGRATIONS, PostgresSupplierMemory, _migration_statements
from app.persistence.repository import ConcurrentRunUpdateError
from app.persistence.sqlite import SqliteSupplierMemory
from app.workflow.models import RfqRunState


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
        self.migration_versions: set[str] = set()
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
        if statement.strip() == "SELECT version FROM quotehawk.schema_migrations":
            return _Cursor(rows=[{"version": version} for version in self.migration_versions])
        if "FROM quotehawk.schema_migrations" in statement:
            version = parameters[0] if parameters else ""
            return _Cursor(row={"present": 1} if version in self.migration_versions else None)
        if statement.startswith("INSERT INTO quotehawk.schema_migrations"):
            assert parameters is not None
            self.migration_versions.add(str(parameters[0]))
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
            connection.migration_versions = self.connections[-1].migration_versions.copy()
        self.connections.append(connection)
        return connection


class _RecordingPool:
    """Offline pool double that verifies borrow/return behavior without sockets."""

    def __init__(self, connection: _RecordingConnection) -> None:
        self._connection = connection
        self.open_calls: list[bool] = []
        self.borrow_count = 0
        self.return_count = 0
        self.closed = False

    def open(self, *, wait: bool) -> None:
        self.open_calls.append(wait)

    @contextmanager
    def connection(self):  # type: ignore[no-untyped-def]
        self.borrow_count += 1
        try:
            yield self._connection
        finally:
            self.return_count += 1

    def close(self) -> None:
        self.closed = True


class _ReplayIdempotencyConnection(_RecordingConnection):
    """Offline Postgres response for a matching existing idempotency record."""

    def execute(self, statement: str, parameters: tuple[object, ...] | None = None) -> _Cursor:
        if "SELECT run_id, request_fingerprint" in statement:
            self.calls.append((statement, parameters))
            return _Cursor(row={"run_id": "tenant-run", "request_fingerprint": "submission-sha-1"})
        return super().execute(statement, parameters)


class _ReplayIdempotencyFactory:
    def __init__(self) -> None:
        self.connections: list[_ReplayIdempotencyConnection] = []

    def __call__(self, _dsn: str) -> _ReplayIdempotencyConnection:
        connection = _ReplayIdempotencyConnection()
        self.connections.append(connection)
        return connection


class _StaleCasConnection:
    """Offline Postgres response for the row-lock then stale-version race."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...] | None]] = []
        self.closed = False

    def transaction(self):  # type: ignore[no-untyped-def]
        return nullcontext()

    def execute(self, statement: str, parameters: tuple[object, ...] | None = None) -> _Cursor:
        self.calls.append((statement, parameters))
        if "SELECT state, version, payload_json" in statement:
            return _Cursor(row={"state": "intake", "version": 0, "payload_json": {}})
        if "UPDATE quotehawk.rfq_runs" in statement:
            cursor = _Cursor()
            cursor.rowcount = 0
            return cursor
        return _Cursor()

    def close(self) -> None:
        self.closed = True


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


def test_postgres_default_runtime_uses_a_lazy_psycopg_pool() -> None:
    repository = PostgresSupplierMemory(
        "postgresql://offline.invalid/quotehawk",
        pool_min_size=0,
        pool_max_size=2,
    )

    assert isinstance(repository._connection_pool, ConnectionPool)
    assert repository._pool_opened is False
    repository.close()


def test_postgres_pool_is_lazy_bounded_and_reuses_borrowed_connections() -> None:
    connection = _RecordingConnection()
    pool = _RecordingPool(connection)
    repository = PostgresSupplierMemory(
        "postgresql://offline/quotehawk",
        connection_pool=pool,
        pool_min_size=1,
        pool_max_size=4,
        pool_timeout_seconds=2,
    )

    assert pool.open_calls == []
    assert repository.pool_min_size == 1
    assert repository.pool_max_size == 4
    assert repository.pool_timeout_seconds == 2

    repository.load_rfq_run("tenant-run", tenant_id="tenant-acme")
    repository.load_rfq_run("tenant-run", tenant_id="tenant-beta")

    assert pool.open_calls == [False]
    assert pool.borrow_count == 2
    assert pool.return_count == 2
    assert connection.closed is False
    tenant_binds = [
        parameters
        for statement, parameters in connection.calls
        if statement == "SELECT set_config('quotehawk.tenant_id', %s, true)"
    ]
    assert tenant_binds == [("tenant-acme",), ("tenant-beta",)]

    repository.close()
    assert pool.closed is True


def test_postgres_rejects_invalid_pool_bounds_without_connecting() -> None:
    factory = _ConnectionFactory()

    with pytest.raises(ValueError, match="minimum"):
        PostgresSupplierMemory(
            "postgresql://offline/quotehawk",
            connection_factory=factory,
            pool_min_size=-1,
        )
    with pytest.raises(ValueError, match="sizes"):
        PostgresSupplierMemory(
            "postgresql://offline/quotehawk",
            connection_factory=factory,
            pool_min_size=2,
            pool_max_size=1,
        )
    with pytest.raises(ValueError, match="timeout"):
        PostgresSupplierMemory(
            "postgresql://offline/quotehawk",
            connection_factory=factory,
            pool_timeout_seconds=0,
        )

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
    inserted_versions = {
        parameters[0]
        for statement, parameters in first.calls
        if "INSERT INTO quotehawk.schema_migrations" in statement and parameters is not None
    }
    assert inserted_versions == {version for version, _ in _MIGRATIONS}
    assert not any("CREATE POLICY" in item for item in second_statements)
    assert first.closed and second.closed


def test_postgres_readiness_requires_all_reviewed_migrations() -> None:
    factory = _ConnectionFactory()
    repository = PostgresSupplierMemory(
        "postgresql://offline/quotehawk", connection_factory=factory
    )

    with pytest.raises(RuntimeError, match="migration is incomplete"):
        repository.readiness_check()
    repository.initialize()
    repository.readiness_check()


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


def test_postgres_idempotency_replay_is_tenant_scoped_and_uses_matching_fingerprint() -> None:
    factory = _ReplayIdempotencyFactory()
    repository = PostgresSupplierMemory(
        "postgresql://offline/quotehawk", connection_factory=factory
    )

    replay = repository.create_or_retrieve_rfq_run(
        run_id="new-client-run",
        requisition_id="PR-POSTGRES",
        tenant_id="tenant-acme",
        payload={"safe": "snapshot"},
        idempotency_key="postgres-idempotency-key-0001",
        request_fingerprint="submission-sha-1",
    )

    assert replay.created is False
    assert replay.run.run_id == "tenant-run"
    creation_connection = factory.connections[0]
    statements = [statement for statement, _ in creation_connection.calls]
    assert any("ON CONFLICT (tenant_id, idempotency_key)" in statement for statement in statements)
    lookup = next(
        parameters
        for statement, parameters in creation_connection.calls
        if "SELECT run_id, request_fingerprint" in statement
    )
    assert lookup == ("tenant-acme", "postgres-idempotency-key-0001")


def test_postgres_stale_compare_and_swap_raises_without_a_duplicate_event() -> None:
    connection = _StaleCasConnection()
    repository = PostgresSupplierMemory(
        "postgresql://offline/quotehawk", connection_factory=lambda _dsn: connection
    )

    with pytest.raises(ConcurrentRunUpdateError, match="changed concurrently"):
        repository.advance_rfq_run(
            run_id="tenant-run",
            tenant_id="tenant-acme",
            to_state=RfqRunState.NORMALIZED,
            event_type="quotes_normalized",
            reason="worker observed a stale version",
        )

    statements = [statement for statement, _ in connection.calls]
    assert any("FOR UPDATE" in statement for statement in statements)
    assert any("AND version = %s" in statement for statement in statements)
    assert not any("INSERT INTO quotehawk.rfq_run_events" in statement for statement in statements)
    assert connection.closed


def test_persistence_factory_requires_a_dsn_and_preserves_explicit_sqlite_mode(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("QUOTEHAWK_PERSISTENCE_BACKEND", "postgres")
    monkeypatch.delenv("QUOTEHAWK_POSTGRES_DSN", raising=False)
    with pytest.raises(ValueError, match="QUOTEHAWK_POSTGRES_DSN"):
        repository_from_environment()

    monkeypatch.setenv("QUOTEHAWK_POSTGRES_DSN", "postgresql://offline/quotehawk")
    monkeypatch.setenv("QUOTEHAWK_POSTGRES_POOL_MIN_SIZE", "2")
    monkeypatch.setenv("QUOTEHAWK_POSTGRES_POOL_MAX_SIZE", "6")
    monkeypatch.setenv("QUOTEHAWK_POSTGRES_POOL_TIMEOUT_SECONDS", "3.5")
    postgres_repository = repository_from_environment()
    assert isinstance(postgres_repository, PostgresSupplierMemory)
    assert postgres_repository.pool_min_size == 2
    assert postgres_repository.pool_max_size == 6
    assert postgres_repository.pool_timeout_seconds == 3.5
    postgres_repository.close()

    monkeypatch.setenv("QUOTEHAWK_PERSISTENCE_BACKEND", "sqlite")
    monkeypatch.setenv("QUOTEHAWK_DATABASE_PATH", str(tmp_path / "quotehawk.sqlite3"))
    assert isinstance(repository_from_environment(), SqliteSupplierMemory)


def test_postgres_resiliency_migration_has_tenant_scoped_idempotency() -> None:
    migration = Path("app/persistence/migrations/0002_workflow_resiliency.sql").read_text()

    assert "ADD COLUMN IF NOT EXISTS idempotency_key" in migration
    assert "ADD COLUMN IF NOT EXISTS request_fingerprint" in migration
    assert "ON quotehawk.rfq_runs (tenant_id, idempotency_key)" in migration
    assert _MIGRATIONS[-1][0] == "0002_workflow_resiliency"


def test_migration_parser_retains_the_reviewed_policy_statements() -> None:
    statements = _migration_statements()

    assert statements[0] == "CREATE SCHEMA IF NOT EXISTS quotehawk"
    assert any(
        "CREATE POLICY purchase_order_drafts_tenant_policy" in statement for statement in statements
    )
