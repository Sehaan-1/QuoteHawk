"""Authentication and tenant-isolation boundaries for QuoteHawk API requests."""

from app.auth.models import (
    DEFAULT_TENANT_ID,
    AuthenticatedPrincipal,
    AuthMode,
    Permission,
)
from app.auth.service import (
    AuthenticationError,
    AuthorizationError,
    RequestAuthenticator,
    authenticator_from_environment,
)

__all__ = [
    "DEFAULT_TENANT_ID",
    "AuthMode",
    "AuthenticatedPrincipal",
    "AuthenticationError",
    "AuthorizationError",
    "Permission",
    "RequestAuthenticator",
    "authenticator_from_environment",
]
