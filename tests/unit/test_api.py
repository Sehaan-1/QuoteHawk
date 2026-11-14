"""FastAPI routes expose only audit facts and guarded, draft-only actions."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import configure_quotehawk_api, router
from app.approval.service import BuyerApprovalPolicy
from app.persistence.sqlite import SqliteSupplierMemory
from tests.conftest import make_quote


def _client(tmp_path) -> TestClient:
    app = FastAPI()
    configure_quotehawk_api(
        app,
        memory=SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3"),
        buyer_policy=BuyerApprovalPolicy({"buyer-1"}),
    )
    app.include_router(router)
    return TestClient(app)


def test_api_requires_an_explicit_key_before_selecting_gemini(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    response = _client(tmp_path).post(
        "/api/rfq/extract",
        json={"provider": "gemini", "raw_quote": "untrusted supplier text"},
    )

    assert response.status_code == 409
    assert "requires GOOGLE_API_KEY or GEMINI_API_KEY" in response.json()["detail"]


def test_api_runs_workflow_streams_audit_and_creates_a_draft(requisition, tmp_path) -> None:
    client = _client(tmp_path)
    submission = {
        "requisition": requisition.model_dump(mode="json"),
        "quotes": [make_quote(quote_id="API-QUOTE").model_dump(mode="json")],
        "observed_on": date(2026, 11, 14).isoformat(),
        "run_id": "api-run",
    }

    created = client.post("/api/rfq/runs", json=submission)
    assert created.status_code == 201
    assert created.json()["run"]["state"] == "negotiation_or_ready"

    duplicate = client.post("/api/rfq/runs", json=submission)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "RFQ run conflicts with an existing persisted record"

    drafted = client.post(
        "/api/rfq/runs/api-run/counter-offers",
        json={
            "vendor_id": "vendor-alpha",
            "target_landed_cost": str(Decimal("1000")),
            "provider": "fixture",
        },
    )
    assert drafted.status_code == 200
    assert drafted.json()["status"] == "draft_only"

    opened = client.post("/api/rfq/runs/api-run/approval-requests")
    assert opened.status_code == 200
    assert opened.json()["run"]["state"] == "awaiting_approval"

    unauthorized = client.post(
        "/api/rfq/runs/api-run/approvals",
        json={
            "buyer_id": "not-allowed",
            "decision": "approve",
            "selected_quote_id": "API-QUOTE",
        },
    )
    assert unauthorized.status_code == 403

    approved = client.post(
        "/api/rfq/runs/api-run/approvals",
        json={
            "buyer_id": "buyer-1",
            "decision": "approve",
            "selected_quote_id": "API-QUOTE",
        },
    )
    assert approved.status_code == 200
    assert approved.json()["purchase_order_draft"]["status"] == "draft_only"

    events = client.get("/api/rfq/runs/api-run/events")
    assert events.status_code == 200
    assert events.headers["content-type"].startswith("text/event-stream")
    assert "event: rfq.audit" in events.text
    assert "buyer_approved_draft" in events.text
    assert "reasoning" not in events.text.lower()

    detail = client.get("/api/rfq/runs/api-run")
    assert detail.status_code == 200
    assert detail.json()["run"]["state"] == "approved_draft"
