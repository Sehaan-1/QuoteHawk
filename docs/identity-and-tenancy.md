# QuoteHawk identity and tenant isolation

> **Scope:** PR 6 adds API-side OIDC bearer-token verification, role checks,
> server-derived approval actors, and tenant-scoped SQLite data. It does not deploy
> an identity provider, build an OAuth login screen, provision users, send vendor
> messages, write to an ERP, or create a binding purchase order.

## Modes

`QUOTEHAWK_AUTH_MODE` is explicit:

| Mode | Intended use | Identity source |
| --- | --- | --- |
| `prototype` (default) | Credential-free local fixtures, existing dashboard/mobile demo | The legacy `buyer_id` body field and `QUOTEHAWK_AUTHORIZED_BUYERS`; **not production auth** |
| `oidc` | A caller already authenticated by an organization identity provider | A signed, verified `Authorization: Bearer` JWT |

In `oidc` mode, QuoteHawk ignores any `buyer_id` supplied in the request body. It
records the verified JWT `sub` as the approval actor and derives the tenant and
roles only from verified token claims.

## OIDC verifier configuration

Set all required values before starting the API in OIDC mode:

```bash
QUOTEHAWK_AUTH_MODE=oidc
QUOTEHAWK_OIDC_ISSUER=https://identity.example.com/
QUOTEHAWK_OIDC_AUDIENCE=quotehawk-api
QUOTEHAWK_OIDC_JWKS_URL=https://identity.example.com/.well-known/jwks.json
```

The JWKS URL must use HTTPS. QuoteHawk accepts only asymmetric algorithms
`RS256`, `ES256`, `ES384`, or `ES512`; `RS256` is the default. It verifies the
signature, `exp`, `iat`, issuer, audience, and non-empty subject before accepting
a principal. Tokens, JWT claims, and authorization headers are never written to
QuoteHawk's structured logs.

Claim names and required roles are configurable without source changes:

```bash
QUOTEHAWK_OIDC_TENANT_CLAIM=tenant_id
QUOTEHAWK_OIDC_ROLES_CLAIM=roles
QUOTEHAWK_OIDC_READ_ROLE=quotehawk.rfq.read
QUOTEHAWK_OIDC_WRITE_ROLE=quotehawk.rfq.write
QUOTEHAWK_OIDC_APPROVE_ROLE=quotehawk.rfq.approve
QUOTEHAWK_OIDC_ALGORITHMS=RS256
```

The verifier accepts either a JSON-array role claim or a space-delimited string
claim. No role supplied in a request body, header other than the signed JWT, or UI
state grants access.

## Capability map

| API operation | OIDC role | Additional server-side control |
| --- | --- | --- |
| Read a run or its SSE audit stream | `quotehawk.rfq.read` | Run tenant must equal verified tenant claim |
| Create a run, extract a quote, or draft a counter-offer | `quotehawk.rfq.write` | For run actions, run tenant must match |
| Open approval review or decide an approval | `quotehawk.rfq.approve` | Run tenant, workflow state, hard budget, and quote eligibility still apply |

`/api/rfq/healthz` remains unauthenticated and contains only the liveness/draft-only
signal. All other RFQ routes require a valid bearer token in OIDC mode. Missing or
invalid bearer credentials return `401` with `WWW-Authenticate: Bearer`; a verified
principal lacking a required role or tenant match receives `403`.

## Tenant boundaries

An RFQ run is created with the verified tenant claim. Subsequent run detail, SSE,
negotiation, approval request, and approval decision operations must present the
same tenant. Supplier observations and quote history are now scoped by tenant as
well, preventing one tenant's historic prices, freight, or anomaly baseline from
influencing another tenant's analysis.

Existing PR-5 SQLite data is upgraded in place:

- old supplier observation and quote-history rows move into the `local-demo`
  tenant while their primary keys are rebuilt to include the tenant;
- existing RFQ runs receive `tenant_id=local-demo`; and
- existing approvals receive clearly marked legacy prototype actor fields.

Back up a local database before any real migration. This is a tested local SQLite
upgrade, not a substitute for the future reviewed Postgres migration process.

## Approval audit semantics

An approval record retains `buyer_id` for backward-compatible presentation. It now
also stores the server-derived `actor_subject`, `actor_tenant_id`, and
`authentication_method` (`prototype` or `oidc`). The PO payload remains literally
`draft_only`; verified identity authorizes a draft decision, not email dispatch,
ERP write-back, purchase execution, or binding PO issuance.

## Deployment handoff requirements

A future deployment owner must provision and operate the identity provider,
application registration, exact issuer/audience, JWKS availability and rotation,
role assignment, user lifecycle, tenant claim governance, and edge TLS. Use a
same-origin browser reverse proxy or a secure token-aware client strategy; do not
place identity-provider client secrets in the Next static export, Expo bundle, or
QuoteHawk environment logs.
