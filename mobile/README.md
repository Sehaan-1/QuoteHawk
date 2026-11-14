# QuoteHawk mobile approval companion

The Expo companion intentionally covers one job: a buyer can load a persisted
RFQ, inspect in-budget choices, and approve a **non-binding** PO draft or reject
the RFQ outcome. It has no send, purchase, supplier-contact, or ERP capability.

## Run locally

```bash
cd mobile
cp .env.example .env
npm install
npm run start
```

Set `EXPO_PUBLIC_QUOTEHAWK_API_BASE_URL` to an address reachable by the simulator
or device. `localhost` from a phone is the phone, not the FastAPI server.

The API's `QUOTEHAWK_AUTHORIZED_BUYERS` allowlist still controls the decision.
`demo-buyer` is the local prototype default and is not production authentication.

## Check

```bash
npm run typecheck
```
