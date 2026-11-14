"""Buyer sign-off is the only path to a non-binding PO draft."""

from __future__ import annotations

from datetime import date

import pytest

from app.approval.models import ApprovalDecision, BuyerApproval
from app.approval.service import ApprovalError, ApprovalService, BuyerApprovalPolicy
from app.persistence.sqlite import SqliteSupplierMemory
from app.workflow.engine import RfqWorkflow
from app.workflow.models import RfqRunState
from tests.conftest import make_quote


def _approval_service(memory: SqliteSupplierMemory) -> ApprovalService:
    return ApprovalService(memory, policy=BuyerApprovalPolicy({"buyer-1"}))


def test_approval_creates_only_a_persisted_non_binding_draft(requisition, tmp_path) -> None:
    memory = SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3")
    RfqWorkflow(memory).run(
        requisition,
        [make_quote(quote_id="APPROVE-ME")],
        observed_on=date(2026, 11, 14),
        run_id="approval-run",
    )
    approvals = _approval_service(memory)

    request = approvals.open_request("approval-run")
    assert request.run.state is RfqRunState.AWAITING_APPROVAL
    assert memory.purchase_order_draft_for("approval-run") is None

    result = approvals.decide(
        "approval-run",
        BuyerApproval(
            buyer_id="buyer-1",
            decision=ApprovalDecision.APPROVE,
            selected_quote_id="APPROVE-ME",
            note="Commercial terms accepted for a draft.",
        ),
    )

    assert result.purchase_order_draft is not None
    assert result.purchase_order_draft.status == "draft_only"
    assert result.purchase_order_draft.source_quote_id == "APPROVE-ME"
    assert memory.load_rfq_run("approval-run").state is RfqRunState.APPROVED_DRAFT
    assert memory.purchase_order_draft_for("approval-run") == result.purchase_order_draft
    assert memory.audit_events_for("approval-run")[-1].event_type == "buyer_approved_draft"


def test_unapproved_or_unauthorized_path_cannot_unlock_a_draft(requisition, tmp_path) -> None:
    memory = SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3")
    RfqWorkflow(memory).run(
        requisition,
        [make_quote(quote_id="AUTH-GATE")],
        observed_on=date(2026, 11, 14),
        run_id="auth-run",
    )
    approvals = _approval_service(memory)

    with pytest.raises(ApprovalError, match="not awaiting"):
        approvals.decide(
            "auth-run",
            BuyerApproval(
                buyer_id="buyer-1",
                decision=ApprovalDecision.APPROVE,
                selected_quote_id="AUTH-GATE",
            ),
        )
    assert memory.purchase_order_draft_for("auth-run") is None

    approvals.open_request("auth-run")
    with pytest.raises(PermissionError, match="not authorized"):
        approvals.decide(
            "auth-run",
            BuyerApproval(
                buyer_id="not-allowed",
                decision=ApprovalDecision.APPROVE,
                selected_quote_id="AUTH-GATE",
            ),
        )
    assert memory.load_rfq_run("auth-run").state is RfqRunState.AWAITING_APPROVAL
    assert memory.purchase_order_draft_for("auth-run") is None


def test_authorized_rejection_is_terminal_and_never_creates_a_draft(requisition, tmp_path) -> None:
    memory = SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3")
    RfqWorkflow(memory).run(
        requisition,
        [make_quote(quote_id="REJECT-ME")],
        observed_on=date(2026, 11, 14),
        run_id="reject-run",
    )
    approvals = _approval_service(memory)
    approvals.open_request("reject-run")

    result = approvals.decide(
        "reject-run",
        BuyerApproval(
            buyer_id="buyer-1",
            decision=ApprovalDecision.REJECT,
            note="Need revised commercial terms.",
        ),
    )

    assert result.purchase_order_draft is None
    assert memory.load_rfq_run("reject-run").state is RfqRunState.REJECTED
    assert memory.purchase_order_draft_for("reject-run") is None


def test_over_budget_quote_cannot_open_the_approval_gate(requisition, tmp_path) -> None:
    memory = SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3")
    RfqWorkflow(memory).run(
        requisition,
        [make_quote(quote_id="OVER-BUDGET", freight_cost="500")],
        observed_on=date(2026, 11, 14),
        run_id="budget-run",
    )

    with pytest.raises(ApprovalError, match="no within-budget"):
        _approval_service(memory).open_request("budget-run")

    assert memory.load_rfq_run("budget-run").state is RfqRunState.NEGOTIATION_OR_READY
