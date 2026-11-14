"""Explicit runtime selection between local SQLite and reviewed Postgres storage."""

from __future__ import annotations

import os
from enum import StrEnum

from app.persistence.postgres import PostgresSupplierMemory
from app.persistence.repository import QuoteHawkRepository
from app.persistence.sqlite import SqliteSupplierMemory


class PersistenceBackend(StrEnum):
    """Backends deliberately supported by this prototype stage."""

    SQLITE = "sqlite"
    POSTGRES = "postgres"


def repository_from_environment() -> QuoteHawkRepository:
    """Build the configured repository without silently falling back to SQLite."""

    backend_value = (
        os.getenv("QUOTEHAWK_PERSISTENCE_BACKEND", PersistenceBackend.SQLITE).strip().lower()
    )
    try:
        backend = PersistenceBackend(backend_value)
    except ValueError as error:
        raise ValueError("QUOTEHAWK_PERSISTENCE_BACKEND must be 'sqlite' or 'postgres'") from error
    if backend is PersistenceBackend.SQLITE:
        return SqliteSupplierMemory(os.getenv("QUOTEHAWK_DATABASE_PATH", "quotehawk.db"))

    dsn = os.getenv("QUOTEHAWK_POSTGRES_DSN", "").strip()
    if not dsn:
        raise ValueError("QUOTEHAWK_POSTGRES_DSN is required when persistence backend is postgres")
    # Postgres migrations are an explicit deployment step. Do not mutate a
    # production schema opportunistically while accepting an API request.
    return PostgresSupplierMemory(dsn)
