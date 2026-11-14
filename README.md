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

- strict Pydantic contracts around untrusted supplier data;
- `Decimal`-based landed-cost math rather than opaque model calculations;
- SQLite supplier memory that persists price, freight, delivery, responsiveness,
  and discount observations across runs;
- an explainable weighted multi-bid comparison that cannot select an over-budget
  quote;
- Google ADK and `google-agents-cli` scaffolding for the optional Gemini agent,
  evaluations, and a later deployment path.

## Current milestone — PR 6

The prototype now adds production-oriented API identity boundaries while retaining
a credential-free local demo path:

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
  approval actor fields; and
- a non-root local runtime container, liveness endpoint, reverse-proxy contract,
  and explicit production identity/Postgres/vendor-mail/ERP integration contracts.

`QUOTEHAWK_AUTH_MODE=prototype` remains a local fixture/demo fallback only. Set
`QUOTEHAWK_AUTH_MODE=oidc` with the documented issuer, audience, and HTTPS JWKS
configuration to require verified identities. QuoteHawk verifies tokens; it does
not host an OAuth login or provision an identity provider. Real document ingestion,
vendor email, ERP execution, cloud deployment, and binding PO capability remain
out of scope.

## Architecture

```text
fixture JSON or optional Gemini structured extraction
                   |
                   v
       strict Pydantic NormalizedQuote
                   |
                   v
 deterministic pricing + budget policy  <--- SQLite supplier/quote history
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
uv sync --all-groups

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

## Local container, logs, and runtime boundary

The included Docker image is for local/integration use, not a cloud deployment.
It runs as a non-root user, uses `/data/quotehawk.db` for its mounted local SQLite
state, keeps cloud telemetry disabled, and offers a side-effect-free health check:

```bash
docker build -t quotehawk .
docker run --rm -p 8080:8080 -v quotehawk-data:/data quotehawk
curl http://localhost:8080/api/rfq/healthz
```

Every API response carries `X-Request-ID` (a safe client value is preserved or one
is generated). Local JSON logs correlate request/run operational facts without
recording bodies, raw supplier content, commercial values, buyer identities,
secrets, exception text, or model reasoning. Uvicorn access logs are disabled in
the container so they cannot become an alternate URL/query log sink.

See [the runtime and production integration contracts](docs/production-integration-contracts.md)
for the reverse-proxy/SSE behavior, production identity, Postgres replacement, and
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
# inspect project metadata
agents-cli info

# run the credentialed Gemini/ADK evaluation loop after configuring a local key
agents-cli eval run

# deployment is intentionally deferred; add it only after explicit approval
agents-cli scaffold enhance . --deployment-target cloud_run
```

The project uses a **prototype-first** posture. It contains no Terraform, CI/CD,
or cloud resource assumptions yet. Do not deploy it until a buyer-authenticated
approval path and production persistence are implemented.

## Guardrails

| Boundary | Rule |
| --- | --- |
| Budget | A quote over `max_landed_cost` remains visible but is never approval-ready. |
| Currency | This prototype rejects currency mismatches; it does not guess FX rates. |
| Fees | Freight and duties must be present explicitly, even when zero. |
| Precision | All money uses `Decimal`; SQLite stores canonical decimal text. |
| Confidentiality | Counter-offers receive only recipient context plus an anonymous target; provider output containing a competitor name is blocked. |
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
6. **Production identity and tenant authorization** — OIDC JWT verification,
   role-gated API capabilities, server-derived approval actors, and tenant-scoped
   local persistence. Identity-provider provisioning and cloud deployment remain
   intentionally deferred.

## Project layout

```text
app/
  agent.py                 # agents-cli scaffolded ADK boundary
  domain/models.py         # strict procurement contracts
  analysis/                # rolling baselines and deterministic anomaly detector
  approval/                # allowlisted buyer gate and PO draft models
  api/                     # REST/SSE routes that emit audit facts only
  ingestion/               # fixture and optional Gemini structured extraction
  negotiation/             # confidential fixture/Gemini counter-offer drafts
  policies/pricing.py      # deterministic landed-cost and ranking policy
  persistence/sqlite.py    # supplier history, RFQ snapshots, approvals, and audit events
  simulation/              # adversarial supplier tactics
  workflow/                # finite-state RFQ orchestration
web/                       # Next.js buyer dashboard; typed relative API client, proxy, static export
mobile/                    # Expo buyer approval companion
tests/unit/                # credential-free policy, API, approval, and workflow tests
.agents-cli-spec.md        # approved product/safety specification
agents-cli-manifest.yaml   # agents-cli scaffold metadata
```

## License

Apache-2.0. See [LICENSE](LICENSE).
