"""RFQ state machine and durable audit-trail tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.persistence.sqlite import SqliteSupplierMemory
from app.policies.pricing import PolicyViolation
from app.workflow.engine import RfqWorkflow
from app.workflow.models import RfqRunState
from app.workflow.state_machine import InvalidStateTransition
from tests.conftest import make_quote


def test_workflow_persists_every_transition_and_anomaly(requisition, tmp_path) -> None:
    memory = SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3")
    memory.record_quote_history(make_quote(quote_id="HIST-1", freight_cost="100"), date(2026, 6, 1))
    memory.record_quote_history(make_quote(quote_id="HIST-2", freight_cost="100"), date(2026, 8, 1))

    result = RfqWorkflow(memory).run(
        requisition,
        [make_quote(quote_id="CURRENT", freight_cost="122")],
        observed_on=date(2026, 11, 14),
        run_id="run-audited",
    )

    assert result.run.state is RfqRunState.NEGOTIATION_OR_READY
    assert result.run.version == 4
    assert result.comparison.ranked_quotes[0].approval_ready is True
    assert [event.event_type for event in result.audit_events] == [
        "run_created",
        "quotes_normalized",
        "supplier_memory_loaded",
        "quotes_analyzed",
        "negotiation_or_readiness_determined",
    ]
    assert [event.to_state for event in result.audit_events] == [
        RfqRunState.INTAKE,
        RfqRunState.NORMALIZED,
        RfqRunState.ENRICHED_WITH_MEMORY,
        RfqRunState.ANALYZED,
        RfqRunState.NEGOTIATION_OR_READY,
    ]
    assert result.anomalies[0].metric.value == "freight_cost"

    snapshot = memory.payload_for("run-audited")
    assert snapshot["comparison"]["requisition_id"] == requisition.requisition_id
    assert snapshot["anomalies"][0]["metric"] == "freight_cost"

    with pytest.raises(InvalidStateTransition, match="negotiation_or_ready to awaiting_approval"):
        memory.advance_rfq_run(
            run_id="run-audited",
            to_state=RfqRunState.AWAITING_APPROVAL,
            event_type="approval_bypass",
            reason="PR 2 has no buyer approval surface.",
        )


def test_workflow_supports_a_five_bid_comparison(requisition, tmp_path) -> None:
    memory = SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3")
    quotes = [
        make_quote(
            quote_id=f"FIVE-{index}",
            vendor_id=f"vendor-{index}",
            unit_price=f"{Decimal('9.50') + Decimal(index) / Decimal('10'):.2f}",
        )
        for index in range(5)
    ]

    result = RfqWorkflow(memory).run(
        requisition,
        quotes,
        observed_on=date(2026, 11, 14),
        run_id="run-five-bids",
    )

    assert len(result.comparison.ranked_quotes) == 5
    assert result.comparison.ranked_quotes[0].quote_id == "FIVE-0"


def test_illegal_transition_is_rejected_and_never_silently_skips_analysis(
    requisition, tmp_path
) -> None:
    memory = SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3")
    memory.create_rfq_run(
        run_id="run-invalid",
        requisition_id=requisition.requisition_id,
        payload={"requisition": requisition.model_dump(mode="json")},
    )

    with pytest.raises(InvalidStateTransition, match="intake to analyzed"):
        memory.advance_rfq_run(
            run_id="run-invalid",
            to_state=RfqRunState.ANALYZED,
            event_type="skip",
            reason="This must not be allowed.",
        )

    assert memory.load_rfq_run("run-invalid").state is RfqRunState.INTAKE
    assert [event.event_type for event in memory.audit_events_for("run-invalid")] == ["run_created"]


def test_failed_quote_is_audited_without_advancing_to_ready(requisition, tmp_path) -> None:
    memory = SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3")
    quote = make_quote().model_copy(
        update={
            "lines": (make_quote().lines[0].model_copy(update={"quoted_quantity": Decimal("99")}),)
        }
    )

    with pytest.raises(PolicyViolation, match="below the requested quantity"):
        RfqWorkflow(memory).run(
            requisition,
            [quote],
            observed_on=date(2026, 11, 14),
            run_id="run-failed",
        )

    assert memory.load_rfq_run("run-failed").state is RfqRunState.ENRICHED_WITH_MEMORY
    assert memory.audit_events_for("run-failed")[-1].event_type == "workflow_failed"
