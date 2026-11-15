# QuoteHawk mobile approval companion

The Expo companion is a focused buyer-review surface, not a reduced copy of the
web dashboard. A procurement manager can load a durable RFQ, page through **every**
ranked bid in five-bid review surfaces, inspect its deterministic landed-cost/FX
breakdown, read the persisted policy audit trail, open the buyer gate, and confirm an approve, documented
eligible-bid override, or reject decision. Approval supports swipe-right or tap,
but always shows a native confirmation before recording a decision.

The app retains at most five opaque recently opened run IDs locally for convenience.
It never stores quotes, buyer IDs, access tokens, supplier documents, or decision
notes. It has no send, purchase, supplier-contact, or ERP capability; every
successful approval creates a **non-binding** `draft_only` PO payload.

## Run locally

Requires Node.js 22.13 or later for Expo SDK 58 and React Native 0.88.

```bash
cd mobile
cp .env.example .env
npm ci
npm run start
```

Set `EXPO_PUBLIC_QUOTEHAWK_API_BASE_URL` to an address reachable by the simulator
or device. `localhost` from a phone is the phone, not the FastAPI server.

The API's `QUOTEHAWK_AUTHORIZED_BUYERS` allowlist still controls the decision.
`demo-buyer` is the local prototype default and is not production authentication.
An OIDC-configured API now derives the buyer from a verified bearer JWT and
ignores the body buyer ID. In that mode every RFQ call except `healthz`, including
the events stream, must carry `Authorization: Bearer <verified access JWT>`. This
Expo app does not implement OAuth login or store tokens; a future mobile
release needs a reviewed native identity flow and an HTTPS API origin. It must not
rely on this allowlist. See
[`../docs/identity-and-tenancy.md`](../docs/identity-and-tenancy.md) and
[`../docs/production-integration-contracts.md`](../docs/production-integration-contracts.md).

## Check

```bash
npm run typecheck
npm run export:web
npm run export:android
npm audit --omit=dev --audit-level=low
```

The export scripts intentionally run Expo in offline mode. They validate the
committed dependency graph without querying Expo's version service; remove the
resulting `dist/` directory after an ad-hoc local export.
