"""In-place SQLite upgrades retained separately from the runtime repository."""

from __future__ import annotations

import sqlite3

from app.auth.models import DEFAULT_TENANT_ID


def _has_column(connection: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    return any(
        row["name"] == column_name
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    )


def migrate_sqlite_schema(connection: sqlite3.Connection) -> None:
    """Upgrade legacy local databases for identity and retry-safe workflow state."""

    if not _has_column(connection, "supplier_observations", "tenant_id"):
        connection.execute(
            "ALTER TABLE supplier_observations RENAME TO supplier_observations_legacy"
        )
        connection.execute(
            """
            CREATE TABLE supplier_observations (
                tenant_id TEXT NOT NULL,
                vendor_id TEXT NOT NULL,
                quote_id TEXT NOT NULL,
                observed_on TEXT NOT NULL,
                currency TEXT NOT NULL,
                average_unit_price TEXT NOT NULL,
                freight_cost TEXT NOT NULL,
                delivery_delay_days INTEGER NOT NULL,
                responsiveness_score TEXT NOT NULL,
                discount_margin TEXT NOT NULL,
                PRIMARY KEY (tenant_id, vendor_id, quote_id)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO supplier_observations (
                tenant_id, vendor_id, quote_id, observed_on, currency, average_unit_price,
                freight_cost, delivery_delay_days, responsiveness_score, discount_margin
            )
            SELECT ?, vendor_id, quote_id, observed_on, currency, average_unit_price,
                   freight_cost, delivery_delay_days, responsiveness_score, discount_margin
            FROM supplier_observations_legacy
            """,
            (DEFAULT_TENANT_ID,),
        )
        connection.execute("DROP TABLE supplier_observations_legacy")

    if not _has_column(connection, "supplier_quote_history", "tenant_id"):
        connection.execute(
            "ALTER TABLE supplier_quote_history RENAME TO supplier_quote_history_legacy"
        )
        connection.execute(
            """
            CREATE TABLE supplier_quote_history (
                tenant_id TEXT NOT NULL,
                vendor_id TEXT NOT NULL,
                quote_id TEXT NOT NULL,
                observed_on TEXT NOT NULL,
                currency TEXT NOT NULL,
                average_unit_price TEXT NOT NULL,
                freight_cost TEXT NOT NULL,
                lead_time_days INTEGER NOT NULL,
                payment_term_days INTEGER NOT NULL,
                PRIMARY KEY (tenant_id, vendor_id, quote_id)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO supplier_quote_history (
                tenant_id, vendor_id, quote_id, observed_on, currency, average_unit_price,
                freight_cost, lead_time_days, payment_term_days
            )
            SELECT ?, vendor_id, quote_id, observed_on, currency, average_unit_price,
                   freight_cost, lead_time_days, payment_term_days
            FROM supplier_quote_history_legacy
            """,
            (DEFAULT_TENANT_ID,),
        )
        connection.execute("DROP TABLE supplier_quote_history_legacy")

    if not _has_column(connection, "rfq_runs", "tenant_id"):
        connection.execute(
            "ALTER TABLE rfq_runs ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'local-demo'"
        )
    if not _has_column(connection, "rfq_approvals", "actor_subject"):
        connection.execute(
            "ALTER TABLE rfq_approvals "
            "ADD COLUMN actor_subject TEXT NOT NULL DEFAULT 'prototype-legacy'"
        )
        connection.execute("UPDATE rfq_approvals SET actor_subject = buyer_id")
    approval_columns = (
        ("actor_tenant_id", "TEXT NOT NULL DEFAULT 'local-demo'"),
        ("authentication_method", "TEXT NOT NULL DEFAULT 'prototype'"),
    )
    for column_name, declaration in approval_columns:
        if not _has_column(connection, "rfq_approvals", column_name):
            connection.execute(f"ALTER TABLE rfq_approvals ADD COLUMN {column_name} {declaration}")

    if not _has_column(connection, "rfq_runs", "idempotency_key"):
        connection.execute("ALTER TABLE rfq_runs ADD COLUMN idempotency_key TEXT")
    if not _has_column(connection, "rfq_runs", "request_fingerprint"):
        connection.execute("ALTER TABLE rfq_runs ADD COLUMN request_fingerprint TEXT")
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_rfq_runs_tenant_idempotency
        ON rfq_runs (tenant_id, idempotency_key)
        WHERE idempotency_key IS NOT NULL
        """
    )
