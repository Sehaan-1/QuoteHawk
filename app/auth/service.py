"""Bearer-token verification and role checks kept separate from procurement policy."""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any, Protocol

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWTError

from app.auth.models import (
    DEFAULT_TENANT_ID,
    AuthenticatedPrincipal,
    AuthMode,
    OidcSettings,
    Permission,
)


class AuthenticationError(ValueError):
    """Raised when a request lacks a valid, verified bearer token."""


class AuthorizationError(PermissionError):
    """Raised when a verified identity lacks a QuoteHawk capability."""


class RequestAuthenticator(Protocol):
    """Boundary for production OIDC or the deliberately limited local fallback."""

    def authenticate(
        self,
        authorization_header: str | None,
        *,
        prototype_buyer_id: str | None = None,
    ) -> AuthenticatedPrincipal:
        """Return a server-derived principal or raise a safe authentication error."""

    def require(self, principal: AuthenticatedPrincipal, permission: Permission) -> None:
        """Check a capability without consulting a client-supplied role or tenant."""


class _SigningKeyProvider(Protocol):
    """Tiny PyJWT seam that keeps signed-token tests fully offline."""

    def get_signing_key_from_jwt(self, token: str) -> Any:
        """Return the verification key selected by the JWT's protected header."""


class PrototypeAuthenticator:
    """Explicit local compatibility mode; it is never production authentication."""

    def __init__(
        self,
        authorized_buyer_ids: frozenset[str],
        *,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> None:
        self.authorized_buyer_ids = authorized_buyer_ids
        self.tenant_id = tenant_id

    def authenticate(
        self,
        authorization_header: str | None,
        *,
        prototype_buyer_id: str | None = None,
    ) -> AuthenticatedPrincipal:
        if authorization_header is not None and authorization_header != "":
            raise AuthenticationError("prototype mode does not accept bearer credentials")
        subject = prototype_buyer_id or "prototype-system"
        if prototype_buyer_id is not None and subject not in self.authorized_buyer_ids:
            raise AuthorizationError("buyer is not authorized to approve this RFQ")
        return AuthenticatedPrincipal(
            subject=subject,
            tenant_id=self.tenant_id,
            auth_mode=AuthMode.PROTOTYPE,
            roles=frozenset(permission.value for permission in Permission),
        )

    def require(self, principal: AuthenticatedPrincipal, permission: Permission) -> None:
        if permission.value not in principal.roles:
            raise AuthorizationError("prototype identity lacks the required capability")


class OidcJwtAuthenticator:
    """Verify asymmetric OIDC access tokens and derive all authorization inputs."""

    def __init__(
        self,
        settings: OidcSettings,
        *,
        signing_key_provider: _SigningKeyProvider | None = None,
    ) -> None:
        self.settings = settings
        self.signing_key_provider = signing_key_provider or PyJWKClient(settings.jwks_url)

    def authenticate(
        self,
        authorization_header: str | None,
        *,
        prototype_buyer_id: str | None = None,
    ) -> AuthenticatedPrincipal:
        if prototype_buyer_id is not None:
            # Existing prototype clients may send this field. In OIDC mode it is
            # ignored and may not influence the actor recorded for an approval.
            prototype_buyer_id = None
        token = self._bearer_token(authorization_header)
        try:
            header = jwt.get_unverified_header(token)
            algorithm = header.get("alg")
            if algorithm not in self.settings.algorithms:
                raise AuthenticationError("bearer token uses an unsupported signing algorithm")
            signing_key = self.signing_key_provider.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=list(self.settings.algorithms),
                audience=self.settings.audience,
                issuer=self.settings.issuer,
                options={"require": ["exp", "iat", "sub"]},
            )
        except AuthenticationError:
            raise
        except (PyJWTError, ValueError, TypeError) as error:
            raise AuthenticationError("bearer token could not be verified") from error

        subject = claims.get("sub")
        tenant_id = claims.get(self.settings.tenant_claim)
        if not isinstance(subject, str) or not subject.strip():
            raise AuthenticationError("verified token has no usable subject")
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise AuthenticationError("verified token has no usable tenant claim")
        return AuthenticatedPrincipal(
            subject=subject,
            tenant_id=tenant_id,
            auth_mode=AuthMode.OIDC,
            roles=frozenset(_role_values(claims.get(self.settings.roles_claim))),
        )

    def require(self, principal: AuthenticatedPrincipal, permission: Permission) -> None:
        required_role = self.settings.role_for(permission)
        if required_role not in principal.roles:
            raise AuthorizationError("verified identity lacks the required QuoteHawk role")

    @staticmethod
    def _bearer_token(authorization_header: str | None) -> str:
        if authorization_header is None:
            raise AuthenticationError("bearer authentication is required")
        scheme, _, token = authorization_header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip() or " " in token.strip():
            raise AuthenticationError("authorization header must use a bearer token")
        return token.strip()


def authenticator_from_environment(
    authorized_buyer_ids: frozenset[str],
) -> RequestAuthenticator:
    """Select strict OIDC only when explicitly configured; retain local demos otherwise."""

    mode_value = os.getenv("QUOTEHAWK_AUTH_MODE", AuthMode.PROTOTYPE.value).strip().lower()
    try:
        mode = AuthMode(mode_value)
    except ValueError as error:
        raise ValueError("QUOTEHAWK_AUTH_MODE must be 'prototype' or 'oidc'") from error
    if mode is AuthMode.PROTOTYPE:
        tenant_id = os.getenv("QUOTEHAWK_PROTOTYPE_TENANT_ID", DEFAULT_TENANT_ID).strip()
        if not tenant_id:
            raise ValueError("QUOTEHAWK_PROTOTYPE_TENANT_ID must not be empty")
        return PrototypeAuthenticator(authorized_buyer_ids, tenant_id=tenant_id)
    return OidcJwtAuthenticator(OidcSettings.from_environment())


def _role_values(value: object) -> Iterable[str]:
    """Accept standard list claims or space-delimited OAuth scope-style claims."""

    if isinstance(value, str):
        return tuple(role for role in value.split() if role)
    if isinstance(value, list | tuple | set | frozenset):
        return tuple(role for role in value if isinstance(role, str) and role)
    return ()
