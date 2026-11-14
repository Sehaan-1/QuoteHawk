# QuoteHawk buyer workspace

The Next.js workspace consumes QuoteHawk's existing `/api/rfq` surface. It is a
buyer tool, not an execution client: every approval result is a persisted
`draft_only` PO payload.

## Run locally

From the repository root, start the API first:

```bash
QUOTEHAWK_OTEL_TO_CLOUD=false uv run uvicorn app.fast_api_app:app --host 0.0.0.0 --port 8000
```

Then start the workspace in another terminal:

```bash
cd web
cp .env.example .env.local
npm install
npm run dev
```

The Next development server proxies its relative `/api/rfq/*` requests to the
configured `QUOTEHAWK_API_ORIGIN`. Browser code never calls `localhost` itself.
Use **Create local fixture** to exercise the five-bid workflow without Gemini
credentials or external execution.

## Static export

```bash
npm run build:static
```

This creates `out/`. A static host must provide a same-origin reverse proxy for
`/api/rfq/*` to the FastAPI backend; that boundary keeps browser-facing requests
relative in deployed preview environments.

## Checks

```bash
npm run typecheck
npm run build
npm run build:static
```

The visual system follows the software-page plan produced with Taste Skill. Its
minimal, unmodified `iris-opens` runtime is retained under `public/taste/` with
its MIT license and notice.
