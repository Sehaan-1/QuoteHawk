"""Explicit runtime selection between local SQLite and reviewed Postgres storage."""

from __future__ import annotations

import os
from enum import StrEnum

from app.persistence.postgres import PostgresSupplierMemory
from app.persistence.redis_stack import RedisSupplierMemory
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
        repository: QuoteHawkRepository = SqliteSupplierMemory(
            os.getenv("QUOTEHAWK_DATABASE_PATH", "quotehawk.db")
        )
    else:
        dsn = os.getenv("QUOTEHAWK_POSTGRES_DSN", "").strip()
        if not dsn:
            raise ValueError(
                "QUOTEHAWK_POSTGRES_DSN is required when persistence backend is postgres"
            )
        # Postgres migrations are an explicit deployment step. Do not mutate a
        # production schema opportunistically while accepting an API request.
        repository = PostgresSupplierMemory(
            dsn,
            pool_min_size=int(os.getenv("QUOTEHAWK_POSTGRES_POOL_MIN_SIZE", "1")),
            pool_max_size=int(os.getenv("QUOTEHAWK_POSTGRES_POOL_MAX_SIZE", "10")),
            pool_timeout_seconds=float(os.getenv("QUOTEHAWK_POSTGRES_POOL_TIMEOUT_SECONDS", "5")),
        )

    redis_url = os.getenv("QUOTEHAWK_REDIS_URL", "").strip()
    if not redis_url:
        return repository
    return RedisSupplierMemory.from_url(
        repository,
        redis_url,
        cache_ttl_seconds=int(os.getenv("QUOTEHAWK_REDIS_CACHE_TTL_SECONDS", "300")),
        queue_name=os.getenv("QUOTEHAWK_REDIS_QUEUE_NAME", "quotehawk:rfq-work"),
    )
