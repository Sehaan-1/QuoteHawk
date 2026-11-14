CREATE SCHEMA IF NOT EXISTS quotehawk;

CREATE TABLE IF NOT EXISTS quotehawk.schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS quotehawk.supplier_observations (
    tenant_id TEXT NOT NULL,
    vendor_id TEXT NOT NULL,
    quote_id TEXT NOT NULL,
    observed_on DATE NOT NULL,
    currency TEXT NOT NULL,
    average_unit_price NUMERIC NOT NULL,
    freight_cost NUMERIC NOT NULL,
    delivery_delay_days INTEGER NOT NULL,
    responsiveness_score NUMERIC NOT NULL,
    discount_margin NUMERIC NOT NULL,
    PRIMARY KEY (tenant_id, vendor_id, quote_id)
);

CREATE INDEX IF NOT EXISTS supplier_observations_tenant_vendor_currency_idx
ON quotehawk.supplier_observations (tenant_id, vendor_id, currency, observed_on, quote_id);

CREATE TABLE IF NOT EXISTS quotehawk.supplier_quote_history (
    tenant_id TEXT NOT NULL,
    vendor_id TEXT NOT NULL,
    quote_id TEXT NOT NULL,
    observed_on DATE NOT NULL,
    currency TEXT NOT NULL,
    average_unit_price NUMERIC NOT NULL,
    freight_cost NUMERIC NOT NULL,
    lead_time_days INTEGER NOT NULL,
    payment_term_days INTEGER NOT NULL,
    PRIMARY KEY (tenant_id, vendor_id, quote_id)
);

CREATE INDEX IF NOT EXISTS supplier_quote_history_tenant_window_idx
ON quotehawk.supplier_quote_history (tenant_id, vendor_id, currency, observed_on, quote_id);

CREATE TABLE IF NOT EXISTS quotehawk.rfq_runs (
    run_id TEXT PRIMARY KEY,
    requisition_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    state TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 0),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    payload_json JSONB NOT NULL,
    UNIQUE (run_id, tenant_id)
);

CREATE INDEX IF NOT EXISTS rfq_runs_tenant_updated_idx
ON quotehawk.rfq_runs (tenant_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS quotehawk.rfq_run_events (
    tenant_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    occurred_at TIMESTAMPTZ NOT NULL,
    event_type TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT,
    reason TEXT NOT NULL,
    details_json JSONB NOT NULL,
    PRIMARY KEY (tenant_id, run_id, sequence),
    FOREIGN KEY (run_id, tenant_id)
        REFERENCES quotehawk.rfq_runs (run_id, tenant_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS quotehawk.rfq_approvals (
    run_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    buyer_id TEXT NOT NULL,
    actor_subject TEXT NOT NULL,
    actor_tenant_id TEXT NOT NULL,
    authentication_method TEXT NOT NULL,
    decision TEXT NOT NULL,
    selected_quote_id TEXT,
    note TEXT,
    decided_at TIMESTAMPTZ NOT NULL,
    CHECK (tenant_id = actor_tenant_id),
    FOREIGN KEY (run_id, tenant_id)
        REFERENCES quotehawk.rfq_runs (run_id, tenant_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS quotehawk.purchase_order_drafts (
    run_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    draft_id TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL,
    payload_json JSONB NOT NULL,
    FOREIGN KEY (run_id, tenant_id)
        REFERENCES quotehawk.rfq_runs (run_id, tenant_id)
        ON DELETE CASCADE
);

ALTER TABLE quotehawk.supplier_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE quotehawk.supplier_quote_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE quotehawk.rfq_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE quotehawk.rfq_run_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE quotehawk.rfq_approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE quotehawk.purchase_order_drafts ENABLE ROW LEVEL SECURITY;

ALTER TABLE quotehawk.supplier_observations FORCE ROW LEVEL SECURITY;
ALTER TABLE quotehawk.supplier_quote_history FORCE ROW LEVEL SECURITY;
ALTER TABLE quotehawk.rfq_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE quotehawk.rfq_run_events FORCE ROW LEVEL SECURITY;
ALTER TABLE quotehawk.rfq_approvals FORCE ROW LEVEL SECURITY;
ALTER TABLE quotehawk.purchase_order_drafts FORCE ROW LEVEL SECURITY;

CREATE POLICY supplier_observations_tenant_policy ON quotehawk.supplier_observations
USING (tenant_id = current_setting('quotehawk.tenant_id', true))
WITH CHECK (tenant_id = current_setting('quotehawk.tenant_id', true));

CREATE POLICY supplier_quote_history_tenant_policy ON quotehawk.supplier_quote_history
USING (tenant_id = current_setting('quotehawk.tenant_id', true))
WITH CHECK (tenant_id = current_setting('quotehawk.tenant_id', true));

CREATE POLICY rfq_runs_tenant_policy ON quotehawk.rfq_runs
USING (tenant_id = current_setting('quotehawk.tenant_id', true))
WITH CHECK (tenant_id = current_setting('quotehawk.tenant_id', true));

CREATE POLICY rfq_run_events_tenant_policy ON quotehawk.rfq_run_events
USING (tenant_id = current_setting('quotehawk.tenant_id', true))
WITH CHECK (tenant_id = current_setting('quotehawk.tenant_id', true));

CREATE POLICY rfq_approvals_tenant_policy ON quotehawk.rfq_approvals
USING (tenant_id = current_setting('quotehawk.tenant_id', true))
WITH CHECK (tenant_id = current_setting('quotehawk.tenant_id', true));

CREATE POLICY purchase_order_drafts_tenant_policy ON quotehawk.purchase_order_drafts
USING (tenant_id = current_setting('quotehawk.tenant_id', true))
WITH CHECK (tenant_id = current_setting('quotehawk.tenant_id', true));
