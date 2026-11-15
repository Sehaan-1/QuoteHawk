# QuoteHawk buyer workspace

The Next.js workspace consumes QuoteHawk's existing `/api/rfq` surface. It is a
buyer tool, not an execution client: every approval result is a persisted
`draft_only` PO payload.

## Run locally

Requires Node.js 20.9 or later for Next.js 16. From the repository root, start
the API first:

```bash
QUOTEHAWK_OTEL_TO_CLOUD=false uv run uvicorn app.fast_api_app:app --host 0.0.0.0 --port 8000 --no-access-log
```

Then start the workspace in another terminal:

```bash
cd web
cp .env.example .env.local
npm ci
npm run dev
```

The Next development server proxies its relative `/api/rfq/*` requests to the
configured `QUOTEHAWK_API_ORIGIN`. Browser code never calls `localhost` itself.
Use **Create local fixture** to exercise the five-bid review workflow without Gemini
credentials or external execution. The workspace never hardcodes an approver: in
local `prototype` mode, enter an ID that the API has allowlisted through
`QUOTEHAWK_AUTHORIZED_BUYERS` when resolving a decision. In OIDC mode, the API
ignores that optional local field and records the verified token subject instead.
A future identity integration must still attach the verified bearer credential at
the same-origin proxy; this UI does not implement a login flow.

The RFQ comparison uses native table and radio controls, the audit record has
keyboard-accessible pagination, and the page provides a skip link, focused error
recovery, and reduced-motion-safe loading states.

## Static export

```bash
npm run build:static
```

This creates `out/`. A static host must provide a same-origin reverse proxy for
`/api/rfq/*` to the FastAPI backend; that boundary keeps browser-facing requests
relative in deployed preview environments. The proxy must preserve
`X-Request-ID`, avoid request-body logging, disable buffering for the SSE events
route, and use an appropriate streaming timeout. Do not replace the relative
browser URL with `localhost`, a private container host, or a Gemini/ERP/vendor
endpoint.

See [`../docs/production-integration-contracts.md`](../docs/production-integration-contracts.md)
and [`../docs/identity-and-tenancy.md`](../docs/identity-and-tenancy.md) for the
reverse-proxy and OIDC contract. In `oidc` mode, every RFQ route except `healthz`
(including the SSE events route) needs `Authorization: Bearer <verified access
JWT>` forwarded unchanged to FastAPI. Static hosting does not make the local buyer
allowlist production authentication, and this static export does not host an OAuth
login or store a bearer token. A future web identity design must acquire the token
through a reviewed flow and attach it in a same-origin/proxy-safe way.

## Checks

```bash
npm run typecheck
npm run build
npm run build:static
npm audit --omit=dev --audit-level=low
```

The visual system follows the software-page plan produced with Taste Skill. Its
minimal, unmodified `iris-opens` runtime is retained under `public/taste/` with
its MIT license and notice.
