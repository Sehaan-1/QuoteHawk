# QuoteHawk runtime and future integration contracts

> **Status:** prototype hardening only. This document describes the contracts a
> future production owner must implement; it does not deploy QuoteHawk, create a
> cloud resource, configure OAuth, send vendor communications, write to an ERP,
> or authorize a binding purchase order.

QuoteHawk's executable boundary remains **draft only**. An approved RFQ exposes a
`PurchaseOrderDraft` whose `status` is literally `draft_only`; it is not an order,
email, or ERP transaction.

## Local container contract

The repository contains a non-root Python container for local/integration use:

```bash
docker build -t quotehawk .
docker run --rm -p 8080:8080 \
  -v quotehawk-data:/data \
  -e QUOTEHAWK_AUTHORIZED_BUYERS=demo-buyer \
  quotehawk
```

- It binds only the API process to `0.0.0.0:${PORT:-8080}` and disables Uvicorn's
  access log. QuoteHawk's own structured operational events are the intended log
  source.
- `QUOTEHAWK_OTEL_TO_CLOUD=false` is explicit in the image. Cloud telemetry stays
  opt-in and needs a separate reviewed deployment decision.
- SQLite defaults to `/data/quotehawk.db`; mount `/data` or the workflow/audit
  memory will disappear with the container. The database and its journal must not
  be exposed through a static host or committed to source control.
- `GET /api/rfq/healthz` is a side-effect-free liveness endpoint used by the image
  health check. It intentionally reveals no supplier, buyer, quote, or model data.
- `GET /api/rfq/readyz` performs a backend readiness check without loading tenant
  data. It returns `503` when persistence is unavailable or the selected Postgres
  schema migrations are incomplete; route it to readiness checks, not liveness.
- SQLite is suitable for one local process. A horizontally scaled service must use
  a production repository implementation rather than sharing a SQLite file over a
  network filesystem.

The image excludes `.env`, local databases, tests, and browser/mobile build trees.
Pass only the minimum runtime configuration through the execution environment or a
future secret manager; never bake a Gemini key into an image layer.

## Safe observability and correlation contract

Every API response sets `X-Request-ID`. QuoteHawk preserves a caller-provided ID
only when it is a bounded opaque identifier containing letters, numbers, `.`, `_`,
or `-`; otherwise it generates one. The application emits JSON lines to stderr
that correlate:

- the request ID, HTTP method, **route template** (not a raw URL/query), status,
  outcome, and duration; and
- the RFQ run ID with non-commercial operational facts such as state, count of
  quotes or eligible options, selected drafting provider, and `draft_only` status.

The logger has an allowlist of fields. It deliberately does **not** serialize
request/response bodies, raw supplier documents, supplier names, quote prices,
target prices, buyer identities, credentials, stack traces, exception text, model
traces, or hidden reasoning. Keep that constraint when forwarding stderr to an
observability product. In particular, do not add request-body logging to a reverse
proxy, APM SDK, error tracker, or custom middleware without a data review.

Persisted RFQ audit events are a separate business record. They retain concise
state-transition facts for a run and must not be treated as application logs or
expanded to contain model reasoning.

## Browser workspace and reverse-proxy contract

The Next.js dashboard always calls relative `/api/rfq/*` URLs. In development,
`web/next.config.ts` proxies those requests to `QUOTEHAWK_API_ORIGIN`. A static
export has no Next server, so its host must provide an equivalent same-origin
reverse proxy:

```text
https://buyer.example.com/api/rfq/*  ->  QuoteHawk API /api/rfq/*
https://buyer.example.com/*          ->  static Next export
```

A future proxy owner must:

1. terminate TLS and route only the `/api/rfq/` prefix to the API, without
   rewriting browser requests to `localhost` or a private container hostname;
2. pass through `X-Request-ID` and response headers, impose a request size limit
   compatible with the 50,000-character extraction limit, and avoid logging bodies;
3. preserve SSE framing for `/runs/{run_id}/events` by disabling response buffering
   for that route and using an appropriate read/idle timeout; and
4. apply browser security headers and a reviewed, exact-origin CORS policy only if
   a browser must call the API cross-origin. Same-origin proxying is preferred.

The Expo app is different: it uses an explicit device-reachable API origin. A future
mobile deployment needs HTTPS and the same identity contract below; it must not
ship a production endpoint protected only by `QUOTEHAWK_AUTHORIZED_BUYERS`.

## Identity and approval contract

QuoteHawk now has an explicit `oidc` API mode that verifies an existing provider's
asymmetrically signed bearer JWT, derives `sub`, tenant, and roles from verified
claims, scopes RFQ/supplier data by tenant, and persists server-derived approval
actor fields. The default `prototype` mode and `QUOTEHAWK_AUTHORIZED_BUYERS`
remain local-demo compatibility only, not production authentication.

See [the identity and tenancy guide](identity-and-tenancy.md) for required
configuration, capability mapping, token-validation requirements, and SQLite
migration behavior. A future deployment owner must still provision and operate the
identity provider, application registration, role/tenant governance, edge TLS,
user lifecycle, and token/JWKS availability. QuoteHawk does not implement an OAuth
login, issue credentials, or store OAuth client secrets.

Do not make a UI control, a model instruction, a header named `admin`, or an OAuth
scope alone equivalent to a policy override. Any future override needs an explicit,
reviewed product requirement and its own durable authorization/audit path.

## Postgres repository and migration contract

QuoteHawk now includes `PostgresSupplierMemory`, selected only when
`QUOTEHAWK_PERSISTENCE_BACKEND=postgres`. Its reviewed initial schema lives at
[`../app/persistence/migrations/0001_quotehawk_postgres.sql`](../app/persistence/migrations/0001_quotehawk_postgres.sql).
The default remains `sqlite` for credential-free local demos.

Before starting an API process against Postgres, a deployment owner must provide a
TLS-protected secret-managed `QUOTEHAWK_POSTGRES_DSN` and run the explicit,
transactional migration command:

```bash
uv run python -m app.persistence.postgres --dsn "$QUOTEHAWK_POSTGRES_DSN"
```

The API never auto-migrates production storage and never silently falls back to
SQLite when the Postgres backend is selected. The migration records its own version
in `quotehawk.schema_migrations`; take a backup and use a reviewed change window
before any future migration version.

The reviewed resiliency migration
[`../app/persistence/migrations/0002_workflow_resiliency.sql`](../app/persistence/migrations/0002_workflow_resiliency.sql)
adds a tenant-scoped unique idempotency key and immutable request fingerprint to RFQ
runs. The API accepts an opaque `Idempotency-Key`, derives the fingerprint from the
validated canonical request server-side, returns an existing run only when the
fingerprint matches, and rejects changed input with `409`; keys and fingerprints are
never emitted to logs. `POST /api/rfq/runs/{run_id}/resume` resumes incomplete
policy-only work from the persisted snapshot. It has no vendor-mail or ERP side effect.

The schema uses Postgres `NUMERIC` for money, JSONB snapshots, tenant-inclusive
supplier-history primary keys, composite RFQ child foreign keys, and unique run,
approval, PO-draft, and event-sequence constraints. The repository preserves:

- tenant-scoped vendor/quote history and rolling queries that exclude an incoming
  quote from its own baseline;
- atomic RFQ row locks plus version compare-and-swap, state transition,
  audit-event sequence, and approval/PO-draft write; and
- Pydantic-compatible run, approval, draft, and SSE read models.

Every Postgres repository transaction runs `set_config('quotehawk.tenant_id', tenant,
true)` and includes its tenant predicate in application SQL. The schema enables and
forces row-level security on business tables with matching `quotehawk.tenant_id`
policies, providing defense in depth against an accidentally unscoped query. The
application database role must not bypass row-level security; restrict its schema
privileges, use TLS, retain backups/encryption/retention controls, and keep the DSN
outside source, client bundles, and logs.

## Vendor mail and ERP integration contract

There is intentionally no SMTP, vendor mailbox, purchase API, or ERP client in
this repository. If a later approved product requirement adds outbound adapters,
they must be separate from extraction/drafting and must require all of the
following before any external side effect:

- a verified buyer identity and a durable, approved RFQ record;
- an explicit human confirmation immediately before dispatch, with the exact
  recipient/payload presented for review;
- idempotency keys, immutable outbox/audit records, retries that cannot duplicate
  a dispatch, and reconciliation of downstream status;
- recipient/tenant allowlists, least-privilege credentials held outside source and
  logs, rate limits, and circuit breakers; and
- a distinct integration status model. An ERP acknowledgement must never mutate
  the current `draft_only` status into a binding order implicitly.

Until a separately reviewed integration exists, users may export or review the PO
draft outside QuoteHawk, but QuoteHawk itself must not claim a message, purchase,
or order was sent or created.

## Production readiness review

Before any deployment proposal, a human owner should review threat modeling,
identity architecture, tenancy, data classification/retention, database migration
and backup plans, proxy/SSE behavior, rate limits, monitoring destinations,
incident response, and the exact vendor/ERP execution design. Re-run the
credential-free regressions and the credentialed `agents-cli eval run` safety
dataset as part of that review.
