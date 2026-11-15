"""HTTP-boundary integration coverage for the real FastAPI and SQLite stack."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.approval.service import BuyerApprovalPolicy
from app.auth.models import OidcSettings
from app.auth.service import OidcJwtAuthenticator
from app.domain.models import PurchaseRequisition
from app.fast_api_app import create_quotehawk_app
from app.persistence.sqlite import SqliteSupplierMemory
from tests.conftest import make_quote


class _StaticSigningKeyProvider:
    """Offline JWKS seam for exercising verified bearer tokens over HTTP."""

    def __init__(self, public_key: rsa.RSAPublicKey) -> None:
        self.public_key = public_key

    def get_signing_key_from_jwt(self, token: str) -> SimpleNamespace:
        del token
        return SimpleNamespace(key=self.public_key)


@pytest.fixture
def prototype_client(tmp_path: Path) -> Iterator[tuple[TestClient, Path]]:
    """Run the production-shaped app lifecycle with a temporary SQLite database."""

    database_path = tmp_path / "quotehawk-integration.sqlite3"
    app = create_quotehawk_app(
        memory=SqliteSupplierMemory(database_path),
        buyer_policy=BuyerApprovalPolicy({"buyer-1"}),
    )
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, database_path


def _submission(requisition: PurchaseRequisition, *, run_id: str) -> dict[str, object]:
    return {
        "requisition": requisition.model_dump(mode="json"),
        "quotes": [make_quote(quote_id="HTTP-QUOTE").model_dump(mode="json")],
        "observed_on": date(2026, 11, 14).isoformat(),
        "run_id": run_id,
    }


def _oidc_settings() -> OidcSettings:
    return OidcSettings(
        issuer="https://identity.example.test",
        audience="quotehawk-api",
        jwks_url="https://identity.example.test/.well-known/jwks.json",
        tenant_claim="tenant_id",
        roles_claim="roles",
        read_role="quotehawk.rfq.read",
        write_role="quotehawk.rfq.write",
        approve_role="quotehawk.rfq.approve",
    )


def _oidc_authenticator() -> tuple[OidcJwtAuthenticator, rsa.RSAPrivateKey]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return (
        OidcJwtAuthenticator(
            _oidc_settings(),
            signing_key_provider=_StaticSigningKeyProvider(private_key.public_key()),
        ),
        private_key,
    )


def _bearer_token(
    private_key: rsa.RSAPrivateKey,
    *,
    subject: str,
    tenant_id: str,
    roles: list[str],
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": subject,
            "tenant_id": tenant_id,
            "roles": roles,
            "iss": "https://identity.example.test",
            "aud": "quotehawk-api",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "integration-test-key"},
    )


def _sse_data(message: str) -> dict[str, Any]:
    data_line = next(line for line in message.splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert isinstance(payload, dict)
    return cast(dict[str, Any], payload)


@pytest.mark.integration
def test_http_workflow_round_trip_uses_sqlite_and_serializes_durable_output(
    prototype_client: tuple[TestClient, Path], requisition: PurchaseRequisition
) -> None:
    client, database_path = prototype_client

    created = client.post("/api/rfq/runs", json=_submission(requisition, run_id="http-round-trip"))
    detail = client.get("/api/rfq/runs/http-round-trip")

    assert created.status_code == 201
    assert created.headers["content-type"].startswith("application/json")
    created_payload = created.json()
    assert created_payload["run"]["state"] == "negotiation_or_ready"
    assert created_payload["run"]["tenant_id"] == "local-demo"
    assert created_payload["comparison"]["ranked_quotes"][0]["landed_cost"] == {
        "currency": "USD",
        "source_currency": "USD",
        "fx_rate": "1",
        "line_total": "1000.00",
        "freight_cost": "100.00",
        "shipping_cost": "0.00",
        "duties_cost": "50.00",
        "quality_penalty": "0.00",
        "lead_time_penalty": "0.00",
        "minimum_order_penalty": "0.00",
        "payment_term_penalty": "0.00",
        "early_payment_discount": "0.00",
        "total_landed_cost": "1150.00",
    }
    assert detail.status_code == 200
    assert detail.json()["payload"]["requisition"]["target_payment_days"] == 30

    with sqlite3.connect(database_path) as connection:
        persisted_run = connection.execute(
            "SELECT tenant_id, state, payload_json FROM rfq_runs WHERE run_id = ?",
            ("http-round-trip",),
        ).fetchone()

    assert persisted_run is not None
    assert persisted_run[:2] == ("local-demo", "negotiation_or_ready")
    persisted_payload = json.loads(persisted_run[2])
    assert (
        persisted_payload["comparison"]["ranked_quotes"][0]["landed_cost"]["total_landed_cost"]
        == "1150.00"
    )


@pytest.mark.integration
def test_http_oidc_enforces_bearer_roles_and_tenant_boundaries(
    tmp_path: Path, requisition: PurchaseRequisition
) -> None:
    authenticator, private_key = _oidc_authenticator()
    app = create_quotehawk_app(
        memory=SqliteSupplierMemory(tmp_path / "oidc-integration.sqlite3"),
        buyer_policy=BuyerApprovalPolicy({"buyer-1"}),
        authenticator=authenticator,
    )
    full_access = _bearer_token(
        private_key,
        subject="verified-buyer",
        tenant_id="tenant-acme",
        roles=["quotehawk.rfq.read", "quotehawk.rfq.write", "quotehawk.rfq.approve"],
    )
    read_only = _bearer_token(
        private_key,
        subject="tenant-reader",
        tenant_id="tenant-acme",
        roles=["quotehawk.rfq.read"],
    )
    other_tenant = _bearer_token(
        private_key,
        subject="other-buyer",
        tenant_id="tenant-globex",
        roles=["quotehawk.rfq.read", "quotehawk.rfq.write", "quotehawk.rfq.approve"],
    )

    with TestClient(app, raise_server_exceptions=True) as client:
        missing_token = client.post(
            "/api/rfq/runs", json=_submission(requisition, run_id="oidc-http-run")
        )
        forbidden_role = client.post(
            "/api/rfq/runs",
            json=_submission(requisition, run_id="read-only-http-run"),
            headers={"Authorization": f"Bearer {read_only}"},
        )
        created = client.post(
            "/api/rfq/runs",
            json=_submission(requisition, run_id="oidc-http-run"),
            headers={"Authorization": f"Bearer {full_access}"},
        )
        cross_tenant = client.get(
            "/api/rfq/runs/oidc-http-run",
            headers={"Authorization": f"Bearer {other_tenant}"},
        )
        owner_detail = client.get(
            "/api/rfq/runs/oidc-http-run",
            headers={"Authorization": f"Bearer {read_only}"},
        )

    assert missing_token.status_code == 401
    assert missing_token.headers["www-authenticate"] == "Bearer"
    assert forbidden_role.status_code == 403
    assert created.status_code == 201
    assert created.json()["run"]["tenant_id"] == "tenant-acme"
    assert cross_tenant.status_code == 403
    assert cross_tenant.json()["detail"] == "identity is not authorized for this RFQ tenant"
    assert owner_detail.status_code == 200


@pytest.mark.integration
def test_http_sse_stream_has_safe_headers_and_reconnects_without_duplicate_audits(
    prototype_client: tuple[TestClient, Path], requisition: PurchaseRequisition
) -> None:
    client, _ = prototype_client
    created = client.post("/api/rfq/runs", json=_submission(requisition, run_id="http-sse-run"))
    assert created.status_code == 201

    with client.stream("GET", "/api/rfq/runs/http-sse-run/events") as events:
        assert events.status_code == 200
        assert events.headers["content-type"].startswith("text/event-stream")
        assert events.headers["cache-control"] == "no-cache"
        assert events.headers["x-accel-buffering"] == "no"
        stream_body = "".join(events.iter_text())

    messages = [message for message in stream_body.split("\n\n") if message]
    audit_messages = [message for message in messages if "event: rfq.audit" in message]
    audit_ids = [
        int(
            next(
                line.removeprefix("id: ")
                for line in message.splitlines()
                if line.startswith("id: ")
            )
        )
        for message in audit_messages
    ]
    landed_cost = _sse_data(
        next(message for message in messages if "event: rfq.landed_cost" in message)
    )

    assert audit_ids == sorted(audit_ids)
    assert audit_ids
    assert landed_cost["currency"] == "USD"
    assert landed_cost["quotes"][0]["landed_cost"]["total_landed_cost"] == "1150.00"
    assert "reasoning" not in stream_body.lower()

    resumed = client.get(
        "/api/rfq/runs/http-sse-run/events",
        headers={"Last-Event-ID": str(audit_ids[-1])},
    )

    assert resumed.status_code == 200
    assert "event: rfq.audit" not in resumed.text
    assert "event: rfq.landed_cost" not in resumed.text
    assert _sse_data(resumed.text) == {"last_sequence": audit_ids[-1]}
