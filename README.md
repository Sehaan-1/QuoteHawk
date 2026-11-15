# QuoteHawk

**QuoteHawk** is a stateful, guarded RFQ-to-PO procurement-agent prototype. It
turns supplier quotes into a reproducible landed-cost comparison, remembers
supplier commercial history locally, and keeps high-stakes actions behind a human
approval gate.

> **Prototype boundary:** QuoteHawk is draft-only. It never sends vendor email,
> writes to an ERP, places an order, or produces a binding purchase order. A
> buyer must approve before a later workflow can unlock a PO *draft*.

## Why this project

Tail-spend sourcing is often trapped in supplier emails, PDFs, and spreadsheets.
QuoteHawk is designed around the control points a production agent needs:

- strict Pydantic contracts around untrusted supplier data, including three-to-five
  CSV, email-text, or PDF quote artifacts;
- `Decimal`-based landed-cost math rather than opaque model calculations, with
  unit price, freight, shipping, duties, lead-time, MOQ, and payment-term effects;
- SQLite supplier memory that persists price, freight, delivery, responsiveness,
  and discount observations across runs;
- an explainable weighted multi-bid comparison that cannot select an over-budget
  quote;
- Google ADK and `google-agents-cli` scaffolding for the optional Gemini agent,
  evaluations, and a later deployment path.

## Current milestones — PRs 7–13

The prototype now includes a production-oriented Postgres persistence path,
retry-safe durable workflow controls, adversarial policy hardening, and audited
client dependency upgrades while retaining credential-free SQLite demos and the
earlier API identity boundaries:

- a Next.js five-bid dashboard with landed-cost breakdowns, budget eligibility,
  supplier-history anomalies, concise SSE audit facts, and a confidential
  counter-offer drafting action;
- deterministic regressions and optional Gemini/ADK evaluation cases for supplier
  prompt injection, malformed commercial data, confidentiality, approval bypass,
  and draft-only execution boundaries;
- safe local JSON observability with `X-Request-ID` request/run correlation and
  no supplier documents, commercial values, secrets, or model reasoning in logs;
- strict OIDC bearer-JWT verification with server-derived subject, tenant, and
  role authorization; tenant-scoped RFQ and supplier-memory data; and immutable
  approval actor fields;
- an explicit Postgres repository with reviewed migration SQL, forced row-level
  tenant security, transaction-local tenant binding, and atomic workflow/approval
  writes, without a silent SQLite fallback;
- tenant-scoped RFQ idempotency keys, immutable request fingerprints, deterministic
  resume/recovery, readiness checks, and SQLite/Postgres concurrency regressions;
- unique RFQ bid identities plus adversarial checks for nested schema injection,
  straightforward competitor-disclosure obfuscation, and false execution claims;
- current audited client dependencies: Next.js 16 for the buyer workspace and Expo
  SDK 58 with validated web/Android companion exports;
- a source-ingestion API for three-to-five CSV, unstructured-email, and PDF
  supplier artifacts, retaining normalized facts rather than raw documents; and
- a non-root local runtime container, liveness endpoint, reverse-proxy contract,
  and explicit production identity/Postgres/vendor-mail/ERP integration contracts.

`QUOTEHAWK_AUTH_MODE=prototype` remains a local fixture/demo fallback only. Set
`QUOTEHAWK_AUTH_MODE=oidc` with the documented issuer, audience, and HTTPS JWKS
configuration to require verified identities. QuoteHawk verifies tokens; it does
not host an OAuth login or provision an identity provider. Raw supplier-source ingestion is local and request-scoped; vendor mailbox
integration, OCR infrastructure, vendor email, ERP execution, cloud deployment,
and binding PO capability remain out of scope. See the
[supplier source-ingestion contract](docs/supplier-source-ingestion.md) for the
supported CSV, email, and PDF request formats.

## Architecture

```text
CSV, plain-email, or PDF supplier artifact
                   |
                   v
rule-based parser or optional Gemini structured extraction
                   |
                   v
       strict Pydantic NormalizedQuote
                   |
                   v
 deterministic pricing + budget policy  <--- SQLite demo / tenant-scoped Postgres history
                   |
                   v
 persisted RFQ state + concise SSE audit events + anomaly analysis
                   |
                   v
 confidential counter-offer draft / eligible approval request
                   |
                   v
 verified tenant-scoped buyer decision -> non-binding PO draft only
                   |
                   v
 Next.js buyer workspace + Expo approval companion
```

The model is not the policy authority. The optional Gemini adapters may propose
structured extraction or counter-offer prose, but validated deterministic Python
remains responsible for cost calculation, scoring, state transitions,
confidentiality, and approval checks.

## Local setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). The project was
scaffolded with [Google agents-cli](https://github.com/google/agents-cli) 1.14.0.

```bash
# one-time tools
uv tool install google-agents-cli

# dependencies (including test dependencies)
uv sync --all-groups --all-extras

# credential-free deterministic tests
uv run pytest

# optional static checks
uv run ruff check .
```

No Gemini key is needed for policy, fixture extraction/drafting, API, or tests.
To opt into a Gemini adapter, copy `.env.example` to `.env` and configure a local
`GOOGLE_API_KEY` or `GEMINI_API_KEY`; never commit either file with a real
secret. `QUOTEHAWK_AUTHORIZED_BUYERS` is used only by the credential-free
`prototype` auth mode. For verified identities, configure `QUOTEHAWK_AUTH_MODE=oidc`
and the required issuer, audience, JWKS URL, tenant claim, and roles described in
[the identity and tenancy guide](docs/identity-and-tenancy.md).

## Postgres persistence path

SQLite remains the zero-credential local default. The optional Postgres repository
is a reviewed integration path, not a cloud deployment. A deployment owner must
first apply the versioned schema and then explicitly select Postgres; the API never
runs migrations automatically or falls back to SQLite:

```bash
export QUOTEHAWK_POSTGRES_DSN='postgresql://quotehawk_app:...@db.example.com/quotehawk?sslmode=require'
uv run python -m app.persistence.postgres --dsn "$QUOTEHAWK_POSTGRES_DSN"
export QUOTEHAWK_PERSISTENCE_BACKEND=postgres
```

The schema uses `NUMERIC`, JSONB snapshots, atomic versioned RFQ transitions, and
forced tenant row-level security. Keep the DSN in a secret manager and follow the
[Postgres migration contract](docs/production-integration-contracts.md#postgres-repository-and-migration-contract)
before any production proposal.

## Local container, logs, and runtime boundary

The included Docker image is for local/integration use, not a cloud deployment.
It runs as a non-root user, uses `/data/quotehawk.db` for its mounted local SQLite
state, keeps cloud telemetry disabled, and offers a side-effect-free health check:

```bash
docker build -t quotehawk .
docker run --rm -p 8080:8080 -v quotehawk-data:/data quotehawk
curl http://localhost:8080/api/rfq/healthz
curl http://localhost:8080/api/rfq/readyz
```

`GET /api/rfq/healthz` is liveness-only. `GET /api/rfq/readyz` checks the selected
persistence backend without returning tenant or commercial data; it returns `503`
until an explicitly selected Postgres backend has its reviewed migrations applied.

`POST /api/rfq/runs` accepts an optional opaque `Idempotency-Key` header (8–128
letters, digits, `.`, `_`, `:`, or `-`). QuoteHawk hashes a canonical server-side
representation of the validated submission. Repeating the same key and input
returns the persisted run with `200` and `idempotent_replay: true`; reusing that
key for different validated input returns `409`. `POST /api/rfq/runs/{run_id}/resume`
continues only incomplete deterministic transitions from the durable snapshot. Both
controls remain draft-only and never transmit vendor messages or create a binding PO.

Every API response carries `X-Request-ID` (a safe client value is preserved or one
is generated). Local JSON logs correlate request/run operational facts without
recording bodies, raw supplier content, commercial values, buyer identities,
secrets, exception text, or model reasoning. Uvicorn access logs are disabled in
the container so they cannot become an alternate URL/query log sink.

See [the runtime and production integration contracts](docs/production-integration-contracts.md)
for the reverse-proxy/SSE behavior, production identity, Postgres migration path, and
future vendor-mail/ERP requirements. The document is a contract only; no deploy,
OAuth, vendor email, ERP write, or binding PO path is implemented here.

## Buyer clients

The workspace and companion are separate from the Python package so their
JavaScript dependencies do not alter the deterministic policy environment.

```bash
# terminal one — API, with local telemetry disabled
QUOTEHAWK_OTEL_TO_CLOUD=false uv run uvicorn app.fast_api_app:app --host 0.0.0.0 --port 8000 --no-access-log

# terminal two — Next.js buyer dashboard at http://localhost:3000
cd web && npm install && npm run dev

# optional Expo approval companion — configure a device-reachable API base first
cd ../mobile && cp .env.example .env && npm install && npm run start
```

The Next development server proxies the dashboard's relative `/api/rfq` calls to
FastAPI. The Expo app uses `EXPO_PUBLIC_QUOTEHAWK_API_BASE_URL`; a physical phone
needs the computer's reachable LAN URL rather than `localhost`. See
[`web/README.md`](web/README.md) and [`mobile/README.md`](mobile/README.md) for
static-export and device notes.

The Next visual system follows a software-page plan created with
[Taste Skill](https://github.com/leonxlnx/taste-skill); its retained moment runtime
has an MIT notice in `web/THIRD_PARTY_NOTICES.md`.

## agents-cli workflow

```bash
# inspect the agents-cli scaffold metadata and run its Python quality gate
agents-cli info
agents-cli lint
agents-cli build

# run the credentialed Gemini/ADK evaluation loop after configuring a local key
agents-cli eval run

# deployment is intentionally deferred; add it only after explicit approval
agents-cli scaffold enhance . --deployment-target cloud_run
```

## Continuous integration

GitHub Actions runs on every branch push and every pull request targeting `main`. It invokes the
reproducible targets in the root `Makefile` and has four independent checks:

- `agents-cli info`, `lint`, and `build` against the committed ADK scaffold;
- locked Python dependency installation, formatting, linting, tests, type and
  spelling checks, lock verification, and wheel archive validation;
- clean Next.js install, typecheck, production/static builds, and production audit;
- clean Expo install, typecheck, offline web/Android exports, and production audit.

Run the same gates locally with `make ci` (or `make ci-python`, `make ci-web`, or
`make ci-mobile`). The workflow has read-only repository permissions, no secrets,
no cloud authentication, and no deployment steps. Configure its four job names as
required status checks in GitHub branch protection to make successful CI mandatory
before merges.

The project remains **prototype-first**: it has CI but no Terraform, CD deployment,
or cloud resource assumptions. Do not deploy it until an identity-provider,
Postgres operations, proxy controls, threat model, and production readiness review
are explicitly approved.

## Guardrails

| Boundary | Rule |
| --- | --- |
| Budget | A quote over `max_landed_cost` remains visible but is never approval-ready. |
| Currency | This prototype rejects currency mismatches; it does not guess FX rates. |
| Fees | Freight, shipping, and duties must be present explicitly, even when zero. |
| Precision | All money uses `Decimal`; SQLite stores canonical decimal text and Postgres stores `NUMERIC`. |
| Confidentiality | Counter-offers receive only recipient context plus an anonymous target; provider output containing a competitor identity or exact commercial term is blocked after presentation-normalized checks. |
| Approval | In OIDC mode, only a verified token subject with the configured approver role and matching tenant can decide an awaiting request; only an eligible quote can yield a draft. The allowlist is local-prototype fallback only. |
| Execution | No SMTP, ERP, purchase, or PO-issuance tool exists in the agent surface; an approved payload is `draft_only`. |
| Untrusted text | Fixture/Gemini extraction treats supplier text as data, rejects unknown/malformed schema fields, and never grants document instructions policy authority. |
| Observability | Local structured logs correlate safe request/run facts only; they exclude bodies, commercial values, credentials, error text, and model reasoning. |

## Planned PR sequence

1. **Foundation and trust boundaries** — complete.
2. **Stateful workflow and anomalies** — complete.
3. **Guarded Gemini negotiation + HITL API** — complete.
4. **Buyer experience** — complete.
5. **Evaluation, observability, deployability** — complete for the local
   prototype: `agents-cli` safety datasets, adversarial/API regressions, safe local
   correlation logs, container hardening, and future integration contracts.
6. **Production identity and tenant authorization** — complete: OIDC JWT
   verification, role-gated API capabilities, server-derived approval actors, and
   tenant-scoped persistence semantics. Identity-provider provisioning remains
   deferred.
7. **Postgres persistence path** — complete: an explicit Postgres repository,
   versioned schema migration, tenant RLS, transaction-local tenant binding, and
   contract tests. Database provisioning and deployment remain deferred.
8. **Workflow resiliency** — complete: tenant-scoped idempotency keys, immutable
   request fingerprints, deterministic resume/recovery, backend readiness checks,
   and SQLite/Postgres optimistic-concurrency regressions. The draft-only boundary
   remains unchanged.
9. **Adversarial policy hardening** — complete: unique RFQ bid identities,
   nested-schema injection coverage, presentation-normalized competitor disclosure
   blocking, broader false-execution claims, and credentialed safety-eval fixtures.
10. **Prototype quality and runtime validation** — complete: offline deterministic
    regression, static client export, proxy contract, packaging, and safe-runtime
    validation are documented without claiming unavailable cloud/container checks.
11. **Client dependency security** — complete: the Next.js buyer workspace and
    Expo companion use audited current dependency trees with zero production audit
    findings.
12. **Supplier source ingestion and complete landed cost** — complete: a bounded
    three-to-five-document CSV/email/PDF RFQ input normalizes without retaining
    raw artifacts; freight, shipping, duties, lead time, MOQ, and payment terms
    are explicit deterministic cost components.
13. **Continuous integration guardrails** — complete: credential-free GitHub
    Actions checks enforce the same Python, agents-cli, web, and mobile quality
    gates exercised locally through the root `Makefile`; deployment remains absent.

## Project layout

```text
app/
  agent.py                 # agents-cli scaffolded ADK boundary
  domain/models.py         # strict procurement contracts
  analysis/                # rolling baselines and deterministic anomaly detector
  approval/                # allowlisted buyer gate and PO draft models
  api/                     # REST/SSE routes that emit audit facts only
  ingestion/               # fixture, CSV/email/PDF, and optional Gemini extraction
  negotiation/             # confidential fixture/Gemini counter-offer drafts
  policies/pricing.py      # deterministic landed-cost and ranking policy
  persistence/             # SQLite demo backend, Postgres repository, and reviewed migrations
  simulation/              # adversarial supplier tactics
  workflow/                # finite-state RFQ orchestration
web/                       # Next.js buyer dashboard; typed relative API client, proxy, static export
mobile/                    # Expo buyer approval companion
.github/workflows/ci.yml   # credential-free push/PR quality gate
Makefile                   # local equivalents of the CI quality targets
tests/unit/                # credential-free policy, API, approval, and workflow tests
.agents-cli-spec.md        # approved product/safety specification
agents-cli-manifest.yaml   # agents-cli scaffold metadata
```

## License

Apache-2.0. See [LICENSE](LICENSE).
