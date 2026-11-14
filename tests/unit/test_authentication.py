"""OIDC verification and tenant authorization stay credential-free in tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import configure_quotehawk_api, router
from app.approval.service import BuyerApprovalPolicy
from app.auth.models import OidcSettings, Permission
from app.auth.service import AuthenticationError, AuthorizationError, OidcJwtAuthenticator
from app.persistence.sqlite import SqliteSupplierMemory
from tests.conftest import make_quote


class _StaticSigningKeyProvider:
    """Offline equivalent of a JWKS client for signed-token regression tests."""

    def __init__(self, public_key: rsa.RSAPublicKey) -> None:
        self.public_key = public_key

    def get_signing_key_from_jwt(self, token: str) -> SimpleNamespace:
        del token
        return SimpleNamespace(key=self.public_key)


def _settings() -> OidcSettings:
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


def _authenticator() -> tuple[OidcJwtAuthenticator, rsa.RSAPrivateKey]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return (
        OidcJwtAuthenticator(
            _settings(),
            signing_key_provider=_StaticSigningKeyProvider(private_key.public_key()),
        ),
        private_key,
    )


def _token(
    private_key: rsa.RSAPrivateKey,
    *,
    subject: str,
    tenant_id: str,
    roles: list[str],
    audience: str = "quotehawk-api",
    issuer: str = "https://identity.example.test",
    expires_at: datetime | None = None,
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": subject,
            "tenant_id": tenant_id,
            "roles": roles,
            "iss": issuer,
            "aud": audience,
            "iat": now,
            "exp": expires_at or now + timedelta(minutes=5),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "offline-test-key"},
    )


def _oidc_client(tmp_path, authenticator: OidcJwtAuthenticator) -> TestClient:
    app = FastAPI()
    configure_quotehawk_api(
        app,
        memory=SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3"),
        buyer_policy=BuyerApprovalPolicy({"demo-buyer"}),
        authenticator=authenticator,
    )
    app.include_router(router)
    return TestClient(app)


def _submission(requisition, *, run_id: str) -> dict[str, object]:
    return {
        "requisition": requisition.model_dump(mode="json"),
        "quotes": [make_quote(quote_id="OIDC-QUOTE").model_dump(mode="json")],
        "run_id": run_id,
    }


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_oidc_settings_require_https_jwks_and_asymmetric_algorithms() -> None:
    settings = _settings()

    with pytest.raises(ValueError, match="must use HTTPS"):
        OidcSettings(
            issuer=settings.issuer,
            audience=settings.audience,
            jwks_url="http://identity.example.test/jwks.json",
            tenant_claim=settings.tenant_claim,
            roles_claim=settings.roles_claim,
            read_role=settings.read_role,
            write_role=settings.write_role,
            approve_role=settings.approve_role,
        )
    with pytest.raises(ValueError, match="asymmetric"):
        OidcSettings(
            issuer=settings.issuer,
            audience=settings.audience,
            jwks_url=settings.jwks_url,
            tenant_claim=settings.tenant_claim,
            roles_claim=settings.roles_claim,
            read_role=settings.read_role,
            write_role=settings.write_role,
            approve_role=settings.approve_role,
            algorithms=("HS256",),
        )


def test_oidc_verifier_derives_subject_tenant_and_roles_from_a_signed_token() -> None:
    authenticator, private_key = _authenticator()
    token = _token(
        private_key,
        subject="buyer-verified",
        tenant_id="tenant-acme",
        roles=["quotehawk.rfq.read", "quotehawk.rfq.write"],
    )

    principal = authenticator.authenticate(
        f"Bearer {token}",
        prototype_buyer_id="untrusted-body-buyer",
    )

    assert principal.subject == "buyer-verified"
    assert principal.tenant_id == "tenant-acme"
    assert "untrusted-body-buyer" not in principal.subject
    authenticator.require(principal, Permission.READ)
    with pytest.raises(AuthorizationError, match="required QuoteHawk role"):
        authenticator.require(principal, Permission.APPROVE)


def test_oidc_verifier_rejects_missing_malformed_and_invalid_tokens() -> None:
    authenticator, private_key = _authenticator()

    with pytest.raises(AuthenticationError, match="bearer authentication"):
        authenticator.authenticate(None)
    with pytest.raises(AuthenticationError, match="could not be verified"):
        authenticator.authenticate("Bearer not-a-jwt")

    wrong_audience = _token(
        private_key,
        subject="buyer-verified",
        tenant_id="tenant-acme",
        roles=["quotehawk.rfq.read"],
        audience="another-api",
    )
    wrong_issuer = _token(
        private_key,
        subject="buyer-verified",
        tenant_id="tenant-acme",
        roles=["quotehawk.rfq.read"],
        issuer="https://another-issuer.example.test",
    )
    expired = _token(
        private_key,
        subject="buyer-verified",
        tenant_id="tenant-acme",
        roles=["quotehawk.rfq.read"],
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    other_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    wrong_signature = _token(
        other_private_key,
        subject="buyer-verified",
        tenant_id="tenant-acme",
        roles=["quotehawk.rfq.read"],
    )
    symmetric = jwt.encode(
        {"sub": "buyer-verified", "tenant_id": "tenant-acme"},
        "not-an-accepted-signing-key-at-least-thirty-two-bytes",
        algorithm="HS256",
    )
    missing_issued_at = jwt.encode(
        {
            "sub": "buyer-verified",
            "tenant_id": "tenant-acme",
            "roles": ["quotehawk.rfq.read"],
            "iss": "https://identity.example.test",
            "aud": "quotehawk-api",
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        private_key,
        algorithm="RS256",
    )

    for token in (
        wrong_audience,
        wrong_issuer,
        expired,
        wrong_signature,
        symmetric,
        missing_issued_at,
    ):
        with pytest.raises(AuthenticationError):
            authenticator.authenticate(f"Bearer {token}")


def test_oidc_api_requires_roles_scopes_runs_by_tenant_and_records_verified_actor(
    requisition, tmp_path
) -> None:
    authenticator, private_key = _authenticator()
    client = _oidc_client(tmp_path, authenticator)
    full_access = _token(
        private_key,
        subject="verified-approver",
        tenant_id="tenant-acme",
        roles=["quotehawk.rfq.read", "quotehawk.rfq.write", "quotehawk.rfq.approve"],
    )
    other_tenant = _token(
        private_key,
        subject="other-tenant-user",
        tenant_id="tenant-globex",
        roles=["quotehawk.rfq.read", "quotehawk.rfq.write", "quotehawk.rfq.approve"],
    )
    read_only = _token(
        private_key,
        subject="acme-reader",
        tenant_id="tenant-acme",
        roles=["quotehawk.rfq.read"],
    )

    missing_token = client.post("/api/rfq/runs", json=_submission(requisition, run_id="oidc-run"))
    assert missing_token.status_code == 401
    assert missing_token.headers["www-authenticate"] == "Bearer"
    assert (
        client.post(
            "/api/rfq/runs",
            json=_submission(requisition, run_id="read-only-run"),
            headers=_headers(read_only),
        ).status_code
        == 403
    )

    created = client.post(
        "/api/rfq/runs",
        json=_submission(requisition, run_id="oidc-run"),
        headers=_headers(full_access),
    )
    assert created.status_code == 201
    assert created.json()["run"]["tenant_id"] == "tenant-acme"

    denied_cross_tenant = client.get("/api/rfq/runs/oidc-run", headers=_headers(other_tenant))
    assert denied_cross_tenant.status_code == 403
    assert client.get("/api/rfq/runs/oidc-run", headers=_headers(read_only)).status_code == 200

    approval_request = client.post(
        "/api/rfq/runs/oidc-run/approval-requests",
        headers=_headers(full_access),
    )
    assert approval_request.status_code == 200
    approved = client.post(
        "/api/rfq/runs/oidc-run/approvals",
        headers=_headers(full_access),
        json={
            "buyer_id": "forged-client-buyer",
            "decision": "approve",
            "selected_quote_id": "OIDC-QUOTE",
        },
    )

    assert approved.status_code == 200
    record = approved.json()["record"]
    assert record["buyer_id"] == "verified-approver"
    assert record["actor_subject"] == "verified-approver"
    assert record["actor_tenant_id"] == "tenant-acme"
    assert record["authentication_method"] == "oidc"
