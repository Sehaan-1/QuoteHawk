"""Strict, server-derived identity contracts used at the API boundary."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_TENANT_ID = "local-demo"


class AuthMode(StrEnum):
    """The only authentication modes supported by this incremental release."""

    PROTOTYPE = "prototype"
    OIDC = "oidc"


class Permission(StrEnum):
    """Role-gated operations; execution is intentionally absent."""

    READ = "read"
    WRITE = "write"
    APPROVE = "approve"


class AuthenticatedPrincipal(BaseModel):
    """A verified server-side identity; never construct it from request JSON."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")

    subject: str = Field(min_length=1, max_length=255)
    tenant_id: str = Field(min_length=1, max_length=120)
    auth_mode: AuthMode
    roles: frozenset[str] = frozenset()


@dataclass(frozen=True)
class OidcSettings:
    """Required OIDC verifier configuration, loaded only in strict OIDC mode."""

    issuer: str
    audience: str
    jwks_url: str
    tenant_claim: str
    roles_claim: str
    read_role: str
    write_role: str
    approve_role: str
    algorithms: tuple[str, ...] = ("RS256",)

    def __post_init__(self) -> None:
        """Reject incomplete or symmetric verifier settings even in direct wiring/tests."""

        required_values = (
            self.issuer,
            self.audience,
            self.jwks_url,
            self.tenant_claim,
            self.roles_claim,
            self.read_role,
            self.write_role,
            self.approve_role,
        )
        if any(not value.strip() for value in required_values):
            raise ValueError("OIDC verifier settings must not contain empty values")
        parsed_jwks_url = urlparse(self.jwks_url)
        if parsed_jwks_url.scheme != "https" or not parsed_jwks_url.netloc:
            raise ValueError("QUOTEHAWK_OIDC_JWKS_URL must use HTTPS")
        allowed_algorithms = frozenset({"RS256", "ES256", "ES384", "ES512"})
        if not self.algorithms or not set(self.algorithms).issubset(allowed_algorithms):
            raise ValueError(
                "QUOTEHAWK_OIDC_ALGORITHMS must contain supported asymmetric algorithms"
            )

    @classmethod
    def from_environment(cls) -> OidcSettings:
        """Read a complete verifier configuration without accepting insecure defaults."""

        required = {
            "QUOTEHAWK_OIDC_ISSUER": os.getenv("QUOTEHAWK_OIDC_ISSUER", "").strip(),
            "QUOTEHAWK_OIDC_AUDIENCE": os.getenv("QUOTEHAWK_OIDC_AUDIENCE", "").strip(),
            "QUOTEHAWK_OIDC_JWKS_URL": os.getenv("QUOTEHAWK_OIDC_JWKS_URL", "").strip(),
        }
        missing = sorted(name for name, value in required.items() if not value)
        if missing:
            raise ValueError(f"OIDC mode requires {', '.join(missing)}")
        jwks_url = required["QUOTEHAWK_OIDC_JWKS_URL"]
        algorithms = tuple(
            algorithm.strip()
            for algorithm in os.getenv("QUOTEHAWK_OIDC_ALGORITHMS", "RS256").split(",")
            if algorithm.strip()
        )
        return cls(
            issuer=required["QUOTEHAWK_OIDC_ISSUER"],
            audience=required["QUOTEHAWK_OIDC_AUDIENCE"],
            jwks_url=jwks_url,
            tenant_claim=os.getenv("QUOTEHAWK_OIDC_TENANT_CLAIM", "tenant_id").strip(),
            roles_claim=os.getenv("QUOTEHAWK_OIDC_ROLES_CLAIM", "roles").strip(),
            read_role=os.getenv("QUOTEHAWK_OIDC_READ_ROLE", "quotehawk.rfq.read").strip(),
            write_role=os.getenv("QUOTEHAWK_OIDC_WRITE_ROLE", "quotehawk.rfq.write").strip(),
            approve_role=os.getenv("QUOTEHAWK_OIDC_APPROVE_ROLE", "quotehawk.rfq.approve").strip(),
            algorithms=algorithms,
        )

    def role_for(self, permission: Permission) -> str:
        """Return the role needed for one protected capability."""

        return {
            Permission.READ: self.read_role,
            Permission.WRITE: self.write_role,
            Permission.APPROVE: self.approve_role,
        }[permission]
