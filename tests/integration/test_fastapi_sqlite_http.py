"""Atomic HTTP-boundary coverage for the real FastAPI and SQLite stack."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
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


@dataclass(frozen=True)
class _PrototypeHttpEnvironment:
    client: TestClient
    database_path: Path


@dataclass(frozen=True)
class _OidcHttpEnvironment:
    client: TestClient
    full_access_headers: dict[str, str]
    read_only_headers: dict[str, str]
    other_tenant_headers: dict[str, str]


@pytest.fixture
def prototype_http_environment(tmp_path: Path) -> Iterator[_PrototypeHttpEnvironment]:
    """Run the production-shaped app lifecycle with temporary SQLite storage."""

    database_path = tmp_path / "quotehawk-integration.sqlite3"
    app = create_quotehawk_app(
        memory=SqliteSupplierMemory(database_path),
        buyer_policy=BuyerApprovalPolicy({"buyer-1"}),
    )
    with TestClient(app, raise_server_exceptions=True) as client:
        yield _PrototypeHttpEnvironment(client=client, database_path=database_path)


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


def _bearer_headers(
    private_key: rsa.RSAPrivateKey,
    *,
    subject: str,
    tenant_id: str,
    roles: list[str],
) -> dict[str, str]:
    now = datetime.now(UTC)
    token = jwt.encode(
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
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def oidc_http_environment(tmp_path: Path) -> Iterator[_OidcHttpEnvironment]:
    """Start a full FastAPI app wired to offline-verifiable OIDC and SQLite."""

    authenticator, private_key = _oidc_authenticator()
    app = create_quotehawk_app(
        memory=SqliteSupplierMemory(tmp_path / "oidc-integration.sqlite3"),
        buyer_policy=BuyerApprovalPolicy({"buyer-1"}),
        authenticator=authenticator,
    )
    full_access_headers = _bearer_headers(
        private_key,
        subject="verified-buyer",
        tenant_id="tenant-acme",
        roles=["quotehawk.rfq.read", "quotehawk.rfq.write", "quotehawk.rfq.approve"],
    )
    read_only_headers = _bearer_headers(
        private_key,
        subject="tenant-reader",
        tenant_id="tenant-acme",
        roles=["quotehawk.rfq.read"],
    )
    other_tenant_headers = _bearer_headers(
        private_key,
        subject="other-buyer",
        tenant_id="tenant-globex",
        roles=["quotehawk.rfq.read", "quotehawk.rfq.write", "quotehawk.rfq.approve"],
    )
    with TestClient(app, raise_server_exceptions=True) as client:
        yield _OidcHttpEnvironment(
            client=client,
            full_access_headers=full_access_headers,
            read_only_headers=read_only_headers,
            other_tenant_headers=other_tenant_headers,
        )


def _create_prototype_run(
    environment: _PrototypeHttpEnvironment,
    requisition: PurchaseRequisition,
    *,
    run_id: str,
) -> None:
    response = environment.client.post(
        "/api/rfq/runs", json=_submission(requisition, run_id=run_id)
    )
    assert response.status_code == 201


def _create_oidc_run(
    environment: _OidcHttpEnvironment,
    requisition: PurchaseRequisition,
    *,
    run_id: str,
) -> None:
    response = environment.client.post(
        "/api/rfq/runs",
        json=_submission(requisition, run_id=run_id),
        headers=environment.full_access_headers,
    )
    assert response.status_code == 201


def _sse_response(
    client: TestClient,
    run_id: str,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], str]:
    with client.stream("GET", f"/api/rfq/runs/{run_id}/events", headers=headers) as response:
        status_code = int(response.status_code)
        response_headers = {str(name): str(value) for name, value in response.headers.items()}
        stream_body = "".join(str(chunk) for chunk in response.iter_text())
    return status_code, response_headers, stream_body


def _sse_messages(stream_body: str) -> list[str]:
    return [message for message in stream_body.split("\n\n") if message]


def _sse_event_name(message: str) -> str:
    return next(
        line.removeprefix("event: ") for line in message.splitlines() if line.startswith("event: ")
    )


def _sse_data(message: str) -> dict[str, Any]:
    data_line = next(line for line in message.splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert isinstance(payload, dict)
    return cast(dict[str, Any], payload)


def _audit_ids(messages: list[str]) -> list[int]:
    return [
        int(
            next(
                line.removeprefix("id: ")
                for line in message.splitlines()
                if line.startswith("id: ")
            )
        )
        for message in messages
        if _sse_event_name(message) == "rfq.audit"
    ]


@pytest.mark.integration
def test_http_health_reports_the_draft_only_boundary(
    prototype_http_environment: _PrototypeHttpEnvironment,
) -> None:
    response = prototype_http_environment.client.get("/api/rfq/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "execution_boundary": "draft_only"}


@pytest.mark.integration
def test_http_create_run_returns_a_successful_workflow_response(
    prototype_http_environment: _PrototypeHttpEnvironment, requisition: PurchaseRequisition
) -> None:
    response = prototype_http_environment.client.post(
        "/api/rfq/runs", json=_submission(requisition, run_id="http-created-run")
    )

    assert response.status_code == 201
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["run"]["state"] == "negotiation_or_ready"


@pytest.mark.integration
def test_http_workflow_serializes_landed_costs_as_exact_decimal_strings(
    prototype_http_environment: _PrototypeHttpEnvironment, requisition: PurchaseRequisition
) -> None:
    response = prototype_http_environment.client.post(
        "/api/rfq/runs", json=_submission(requisition, run_id="http-serialized-run")
    )

    landed_cost = response.json()["comparison"]["ranked_quotes"][0]["landed_cost"]

    assert landed_cost["line_total"] == "1000.00"
    assert landed_cost["fx_rate"] == "1"
    assert landed_cost["total_landed_cost"] == "1150.00"


@pytest.mark.integration
def test_http_workflow_persists_the_run_in_sqlite(
    prototype_http_environment: _PrototypeHttpEnvironment, requisition: PurchaseRequisition
) -> None:
    _create_prototype_run(prototype_http_environment, requisition, run_id="http-persisted-run")

    with sqlite3.connect(prototype_http_environment.database_path) as connection:
        persisted_run = connection.execute(
            "SELECT tenant_id, state FROM rfq_runs WHERE run_id = ?",
            ("http-persisted-run",),
        ).fetchone()

    assert persisted_run == ("local-demo", "negotiation_or_ready")


@pytest.mark.integration
def test_http_workflow_detail_returns_the_persisted_payment_preference(
    prototype_http_environment: _PrototypeHttpEnvironment, requisition: PurchaseRequisition
) -> None:
    _create_prototype_run(prototype_http_environment, requisition, run_id="http-detail-run")

    response = prototype_http_environment.client.get("/api/rfq/runs/http-detail-run")

    assert response.status_code == 200
    assert response.json()["payload"]["requisition"]["target_payment_days"] == 30


@pytest.mark.integration
def test_http_prototype_mode_rejects_bearer_credentials(
    prototype_http_environment: _PrototypeHttpEnvironment, requisition: PurchaseRequisition
) -> None:
    response = prototype_http_environment.client.post(
        "/api/rfq/runs",
        json=_submission(requisition, run_id="prototype-bearer-run"),
        headers={"Authorization": "Bearer not-accepted-in-prototype"},
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.integration
def test_http_oidc_rejects_a_missing_bearer_token(
    oidc_http_environment: _OidcHttpEnvironment, requisition: PurchaseRequisition
) -> None:
    response = oidc_http_environment.client.post(
        "/api/rfq/runs", json=_submission(requisition, run_id="missing-token-run")
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.integration
def test_http_oidc_rejects_a_malformed_authorization_scheme(
    oidc_http_environment: _OidcHttpEnvironment, requisition: PurchaseRequisition
) -> None:
    response = oidc_http_environment.client.post(
        "/api/rfq/runs",
        json=_submission(requisition, run_id="malformed-token-run"),
        headers={"Authorization": "Basic not-a-bearer-token"},
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.integration
def test_http_oidc_rejects_a_read_only_token_for_run_creation(
    oidc_http_environment: _OidcHttpEnvironment, requisition: PurchaseRequisition
) -> None:
    response = oidc_http_environment.client.post(
        "/api/rfq/runs",
        json=_submission(requisition, run_id="read-only-token-run"),
        headers=oidc_http_environment.read_only_headers,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "verified identity lacks the required QuoteHawk role"


@pytest.mark.integration
def test_http_oidc_write_token_creates_a_tenant_scoped_run(
    oidc_http_environment: _OidcHttpEnvironment, requisition: PurchaseRequisition
) -> None:
    response = oidc_http_environment.client.post(
        "/api/rfq/runs",
        json=_submission(requisition, run_id="oidc-created-run"),
        headers=oidc_http_environment.full_access_headers,
    )

    assert response.status_code == 201
    assert response.json()["run"]["tenant_id"] == "tenant-acme"


@pytest.mark.integration
def test_http_oidc_reader_can_retrieve_a_run_in_its_own_tenant(
    oidc_http_environment: _OidcHttpEnvironment, requisition: PurchaseRequisition
) -> None:
    _create_oidc_run(oidc_http_environment, requisition, run_id="oidc-own-tenant-run")

    response = oidc_http_environment.client.get(
        "/api/rfq/runs/oidc-own-tenant-run",
        headers=oidc_http_environment.read_only_headers,
    )

    assert response.status_code == 200
    assert response.json()["run"]["tenant_id"] == "tenant-acme"


@pytest.mark.integration
def test_http_oidc_hides_a_run_from_another_tenant(
    oidc_http_environment: _OidcHttpEnvironment, requisition: PurchaseRequisition
) -> None:
    _create_oidc_run(oidc_http_environment, requisition, run_id="oidc-hidden-run")

    response = oidc_http_environment.client.get(
        "/api/rfq/runs/oidc-hidden-run",
        headers=oidc_http_environment.other_tenant_headers,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "identity is not authorized for this RFQ tenant"


@pytest.mark.integration
def test_http_oidc_hides_sse_events_from_another_tenant(
    oidc_http_environment: _OidcHttpEnvironment, requisition: PurchaseRequisition
) -> None:
    _create_oidc_run(oidc_http_environment, requisition, run_id="oidc-hidden-events-run")

    response = oidc_http_environment.client.get(
        "/api/rfq/runs/oidc-hidden-events-run/events",
        headers=oidc_http_environment.other_tenant_headers,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "identity is not authorized for this RFQ tenant"


@pytest.mark.integration
def test_http_oidc_approval_records_the_verified_actor_not_the_body_buyer(
    oidc_http_environment: _OidcHttpEnvironment, requisition: PurchaseRequisition
) -> None:
    run_id = "oidc-approval-run"
    _create_oidc_run(oidc_http_environment, requisition, run_id=run_id)
    opened = oidc_http_environment.client.post(
        f"/api/rfq/runs/{run_id}/approval-requests",
        headers=oidc_http_environment.full_access_headers,
    )
    response = oidc_http_environment.client.post(
        f"/api/rfq/runs/{run_id}/approvals",
        headers=oidc_http_environment.full_access_headers,
        json={
            "buyer_id": "forged-client-buyer",
            "decision": "approve",
            "selected_quote_id": "HTTP-QUOTE",
        },
    )

    assert opened.status_code == 200
    assert response.status_code == 200
    assert response.json()["record"]["buyer_id"] == "verified-buyer"
    assert response.json()["record"]["actor_tenant_id"] == "tenant-acme"


@pytest.mark.integration
def test_http_sse_response_sets_streaming_cache_headers(
    prototype_http_environment: _PrototypeHttpEnvironment, requisition: PurchaseRequisition
) -> None:
    _create_prototype_run(prototype_http_environment, requisition, run_id="sse-header-run")

    status_code, headers, _ = _sse_response(prototype_http_environment.client, "sse-header-run")

    assert status_code == 200
    assert headers["content-type"].startswith("text/event-stream")
    assert headers["cache-control"] == "no-cache"
    assert headers["x-accel-buffering"] == "no"


@pytest.mark.integration
def test_http_sse_streams_audits_with_monotonic_event_ids(
    prototype_http_environment: _PrototypeHttpEnvironment, requisition: PurchaseRequisition
) -> None:
    _create_prototype_run(prototype_http_environment, requisition, run_id="sse-audit-run")

    _, _, stream_body = _sse_response(prototype_http_environment.client, "sse-audit-run")
    audit_ids = _audit_ids(_sse_messages(stream_body))

    assert audit_ids == sorted(audit_ids)
    assert audit_ids == list(range(1, len(audit_ids) + 1))


@pytest.mark.integration
def test_http_sse_streams_a_safe_landed_cost_projection(
    prototype_http_environment: _PrototypeHttpEnvironment, requisition: PurchaseRequisition
) -> None:
    _create_prototype_run(prototype_http_environment, requisition, run_id="sse-landed-cost-run")

    _, _, stream_body = _sse_response(prototype_http_environment.client, "sse-landed-cost-run")
    landed_cost_message = next(
        message
        for message in _sse_messages(stream_body)
        if _sse_event_name(message) == "rfq.landed_cost"
    )
    landed_cost = _sse_data(landed_cost_message)

    assert landed_cost["currency"] == "USD"
    assert landed_cost["quotes"][0]["landed_cost"]["total_landed_cost"] == "1150.00"
    assert "reasoning" not in stream_body.lower()


@pytest.mark.integration
def test_http_sse_reconnect_after_the_last_audit_skips_prior_events(
    prototype_http_environment: _PrototypeHttpEnvironment, requisition: PurchaseRequisition
) -> None:
    _create_prototype_run(prototype_http_environment, requisition, run_id="sse-resume-run")
    _, _, stream_body = _sse_response(prototype_http_environment.client, "sse-resume-run")
    last_audit_id = _audit_ids(_sse_messages(stream_body))[-1]

    status_code, _, resumed_body = _sse_response(
        prototype_http_environment.client,
        "sse-resume-run",
        headers={"Last-Event-ID": str(last_audit_id)},
    )

    assert status_code == 200
    assert [_sse_event_name(message) for message in _sse_messages(resumed_body)] == ["rfq.complete"]
    assert _sse_data(resumed_body) == {"last_sequence": last_audit_id}


@pytest.mark.integration
def test_http_sse_invalid_last_event_id_replays_the_available_audits(
    prototype_http_environment: _PrototypeHttpEnvironment, requisition: PurchaseRequisition
) -> None:
    _create_prototype_run(prototype_http_environment, requisition, run_id="sse-invalid-resume-run")

    _, _, stream_body = _sse_response(
        prototype_http_environment.client,
        "sse-invalid-resume-run",
        headers={"Last-Event-ID": "not-a-sequence"},
    )

    assert _audit_ids(_sse_messages(stream_body))


@pytest.mark.integration
def test_http_sse_complete_frame_reports_the_latest_audit_sequence(
    prototype_http_environment: _PrototypeHttpEnvironment, requisition: PurchaseRequisition
) -> None:
    _create_prototype_run(prototype_http_environment, requisition, run_id="sse-complete-run")

    _, _, stream_body = _sse_response(prototype_http_environment.client, "sse-complete-run")
    messages = _sse_messages(stream_body)

    assert _sse_event_name(messages[-1]) == "rfq.complete"
    assert _sse_data(messages[-1]) == {"last_sequence": _audit_ids(messages)[-1]}
