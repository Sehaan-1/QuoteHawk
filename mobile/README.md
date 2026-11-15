# QuoteHawk mobile approval companion

The Expo companion intentionally covers one job: a buyer can load a persisted
RFQ, inspect in-budget choices, and approve a **non-binding** PO draft or reject
the RFQ outcome. It has no send, purchase, supplier-contact, or ERP capability.

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
Expo prototype does not implement OAuth login or store tokens; a future mobile
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
