"""Buyer-decision webhook stays signed, idempotent, and draft-only."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import pytest

from app.approval.models import ApprovalDecision, ApprovalRecord, BuyerApproval
from app.approval.service import ApprovalService, BuyerApprovalPolicy
from app.approval.webhook import ApprovalWebhookError, BuyerApprovalWebhook
from app.auth.models import AuthMode
from app.persistence.sqlite import SqliteSupplierMemory
from app.workflow.engine import RfqWorkflow
from tests.conftest import make_quote


@dataclass
class FakeTransport:
    status: int = 202
    calls: list[tuple[str, bytes, dict[str, str]]] = field(default_factory=list)

    def post(self, url: str, body: bytes, headers: dict[str, str]) -> int:
        self.calls.append((url, body, headers))
        return self.status


def _record() -> ApprovalRecord:
    return ApprovalRecord(
        run_id="run-1",
        buyer_id="buyer-1",
        actor_subject="buyer-1",
        actor_tenant_id="tenant-1",
        authentication_method=AuthMode.PROTOTYPE,
        decision=ApprovalDecision.APPROVE,
        selected_quote_id="quote-1",
        decided_at=datetime(2026, 11, 14, tzinfo=UTC),
    )


def test_webhook_has_idempotency_signature_and_no_vendor_or_erp_payload() -> None:
    transport = FakeTransport()
    webhook = BuyerApprovalWebhook(
        url="https://workflow.example.test/approval",
        signing_secret="test-secret",
        transport=transport,
    )
    event = webhook.event_for(record=_record(), purchase_order_draft=None)

    webhook.dispatch(event)

    url, body, headers = transport.calls[0]
    payload = json.loads(body)
    assert url == "https://workflow.example.test/approval"
    assert headers["Idempotency-Key"] == str(event.event_id)
    expected = hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    assert headers["X-QuoteHawk-Signature"] == f"sha256={expected}"
    assert payload["execution_boundary"] == "draft_only"
    assert payload["purchase_order_draft_id"] is None
    assert "vendor" not in payload
    assert "erp" not in payload
    assert "message" not in payload


def test_webhook_marks_a_durable_override_explicitly() -> None:
    webhook = BuyerApprovalWebhook(
        url="https://workflow.example.test/approval",
        signing_secret="test-secret",
        transport=FakeTransport(),
    )
    override = _record().model_copy(
        update={
            "decision": ApprovalDecision.OVERRIDE,
            "note": "Override: qualified incumbent is required.",
        }
    )

    assert (
        webhook.event_for(record=override, purchase_order_draft=None).event_type == "buyer_override"
    )


def test_webhook_rejects_non_success_status() -> None:
    webhook = BuyerApprovalWebhook(
        url="https://workflow.example.test/approval",
        signing_secret="test-secret",
        transport=FakeTransport(status=500),
    )

    with pytest.raises(ApprovalWebhookError, match="non-success"):
        webhook.dispatch(webhook.event_for(record=_record(), purchase_order_draft=None))


def test_webhook_failure_is_audited_after_the_draft_only_decision(requisition, tmp_path) -> None:
    memory = SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3")
    RfqWorkflow(memory).run(
        requisition,
        [make_quote(quote_id="WEBHOOK-QUOTE")],
        observed_on=date(2026, 11, 14),
        run_id="webhook-run",
    )
    webhook = BuyerApprovalWebhook(
        url="https://workflow.example.test/approval",
        signing_secret="test-secret",
        transport=FakeTransport(status=503),
    )
    approvals = ApprovalService(
        memory,
        policy=BuyerApprovalPolicy({"buyer-1"}),
        webhook=webhook,
    )
    approvals.open_request("webhook-run")

    with pytest.raises(ApprovalWebhookError, match="non-success"):
        approvals.decide(
            "webhook-run",
            BuyerApproval(
                buyer_id="buyer-1",
                decision=ApprovalDecision.APPROVE,
                selected_quote_id="WEBHOOK-QUOTE",
            ),
        )

    assert memory.purchase_order_draft_for("webhook-run") is not None
    assert memory.audit_events_for("webhook-run")[-1].event_type == "buyer_approval_webhook_failed"
