ALTER TABLE quotehawk.rfq_runs
ADD COLUMN IF NOT EXISTS idempotency_key TEXT;

ALTER TABLE quotehawk.rfq_runs
ADD COLUMN IF NOT EXISTS request_fingerprint TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS rfq_runs_tenant_idempotency_idx
ON quotehawk.rfq_runs (tenant_id, idempotency_key)
WHERE idempotency_key IS NOT NULL;
