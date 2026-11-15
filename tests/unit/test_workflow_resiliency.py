"""Retry, recovery, and stale-update regression coverage for durable RFQ work."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from contextlib import AbstractContextManager
from datetime import date
from pathlib import Path
from types import TracebackType
from typing import Any

import pytest
from typing_extensions import override

from app.auth.models import DEFAULT_TENANT_ID
from app.persistence.repository import ConcurrentRunUpdateError, IdempotencyConflictError
from app.persistence.sqlite import SqliteSupplierMemory
from app.workflow.engine import RfqWorkflow
from app.workflow.models import RfqRun, RfqRunState
from tests.conftest import make_quote


class _InterruptedAfterAnalysisMemory(SqliteSupplierMemory):
    """Inject one worker loss at the post-analysis persistence boundary."""

    def __init__(self, database_path: str | Path) -> None:
        super().__init__(database_path)
        self.interrupt = True

    @override
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
        if self.interrupt and to_state is RfqRunState.NEGOTIATION_OR_READY:
            self.interrupt = False
            raise RuntimeError("simulated worker loss")
        return super().advance_rfq_run(
            run_id=run_id,
            to_state=to_state,
            event_type=event_type,
            reason=reason,
            tenant_id=tenant_id,
            details=details,
            payload_patch=payload_patch,
        )


def test_idempotent_run_replays_persisted_result_without_duplicate_events(
    requisition, tmp_path
) -> None:
    memory = SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3")
    workflow = RfqWorkflow(memory)
    first = workflow.run(
        requisition,
        [make_quote(quote_id="RETRY-QUOTE")],
        observed_on=date(2026, 11, 14),
        run_id="retry-first-run",
        idempotency_key="rfq-retry-key-0001",
        request_fingerprint="submission-sha-1",
    )
    replay = workflow.run(
        requisition,
        [make_quote(quote_id="RETRY-QUOTE")],
        observed_on=date(2026, 11, 14),
        run_id="different-client-run-id",
        idempotency_key="rfq-retry-key-0001",
        request_fingerprint="submission-sha-1",
    )

    assert first.run.run_id == replay.run.run_id == "retry-first-run"
    assert replay.idempotent_replay is True
    assert [event.event_type for event in replay.audit_events] == [
        "run_created",
        "quotes_normalized",
        "supplier_memory_loaded",
        "quotes_analyzed",
        "negotiation_or_readiness_determined",
    ]


def test_idempotency_key_is_scoped_to_the_tenant(requisition, tmp_path) -> None:
    memory = SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3")
    workflow = RfqWorkflow(memory)

    tenant_a = workflow.run(
        requisition,
        [make_quote(quote_id="TENANT-A-QUOTE")],
        observed_on=date(2026, 11, 14),
        tenant_id="tenant-a",
        idempotency_key="shared-idempotency-key-01",
        request_fingerprint="tenant-a-fingerprint",
    )
    tenant_b = workflow.run(
        requisition,
        [make_quote(quote_id="TENANT-B-QUOTE")],
        observed_on=date(2026, 11, 14),
        tenant_id="tenant-b",
        idempotency_key="shared-idempotency-key-01",
        request_fingerprint="tenant-b-fingerprint",
    )

    assert tenant_a.run.tenant_id == "tenant-a"
    assert tenant_b.run.tenant_id == "tenant-b"
    assert tenant_a.run.run_id != tenant_b.run.run_id


def test_idempotency_key_rejects_a_different_submission(requisition, tmp_path) -> None:
    workflow = RfqWorkflow(SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3"))
    workflow.run(
        requisition,
        [make_quote(quote_id="CONFLICT-ONE")],
        observed_on=date(2026, 11, 14),
        idempotency_key="rfq-conflict-key-0001",
        request_fingerprint="submission-sha-one",
    )

    with pytest.raises(IdempotencyConflictError, match="different RFQ submission"):
        workflow.run(
            requisition,
            [make_quote(quote_id="CONFLICT-TWO")],
            observed_on=date(2026, 11, 14),
            idempotency_key="rfq-conflict-key-0001",
            request_fingerprint="submission-sha-two",
        )


def test_resume_uses_saved_analysis_snapshot_after_interruption(requisition, tmp_path) -> None:
    memory = _InterruptedAfterAnalysisMemory(tmp_path / "quotehawk.sqlite3")
    workflow = RfqWorkflow(memory)

    with pytest.raises(RuntimeError, match="simulated worker loss"):
        workflow.run(
            requisition,
            [make_quote(quote_id="RECOVERY-QUOTE")],
            observed_on=date(2026, 11, 14),
            run_id="recovery-run",
        )

    assert memory.load_rfq_run("recovery-run").state is RfqRunState.ANALYZED
    recovered = RfqWorkflow(memory).resume("recovery-run")

    assert recovered.run.state is RfqRunState.NEGOTIATION_OR_READY
    assert recovered.comparison.ranked_quotes[0].quote_id == "RECOVERY-QUOTE"
    assert [event.event_type for event in recovered.audit_events].count("quotes_analyzed") == 1
    assert [event.event_type for event in recovered.audit_events].count(
        "negotiation_or_readiness_determined"
    ) == 1


def test_stale_compare_and_swap_is_reported_as_a_retryable_concurrency_error(
    requisition, tmp_path, monkeypatch
) -> None:
    memory = SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3")
    memory.create_rfq_run(
        run_id="cas-run",
        requisition_id=requisition.requisition_id,
        payload={"requisition": requisition.model_dump(mode="json")},
    )

    # A competing worker can advance the row after this worker reads its version.
    # Simulate that compare-and-swap miss at the database boundary; no duplicate
    # audit event is emitted for the losing transition.
    original_connection = memory._connection

    class _NoUpdateCursor:
        rowcount: int = 0

    class _StaleUpdateConnection:
        def __init__(self, connection_context: AbstractContextManager[sqlite3.Connection]) -> None:
            self.connection_context = connection_context
            self.connection: sqlite3.Connection | None = None
            self.update_seen = False

        def execute(
            self, statement: str, parameters: tuple[object, ...] = ()
        ) -> sqlite3.Cursor | _NoUpdateCursor:
            assert self.connection is not None
            if "UPDATE rfq_runs" in statement and not self.update_seen:
                self.update_seen = True
                return _NoUpdateCursor()
            return self.connection.execute(statement, parameters)

        def __enter__(self) -> _StaleUpdateConnection:
            self.connection = self.connection_context.__enter__()
            return self

        def __exit__(
            self,
            exception_type: type[BaseException] | None,
            exception: BaseException | None,
            traceback: TracebackType | None,
        ) -> bool | None:
            return self.connection_context.__exit__(exception_type, exception, traceback)

    def stale_connection() -> _StaleUpdateConnection:
        return _StaleUpdateConnection(original_connection())

    monkeypatch.setattr(memory, "_connection", stale_connection)
    with pytest.raises(ConcurrentRunUpdateError, match="changed concurrently"):
        memory.advance_rfq_run(
            run_id="cas-run",
            to_state=RfqRunState.NORMALIZED,
            event_type="quotes_normalized",
            reason="stale worker attempt",
        )
    assert [event.event_type for event in memory.audit_events_for("cas-run")] == ["run_created"]
