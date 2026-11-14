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

## Current milestone — PR 1

This foundation ships the deterministic trust boundary:

- `PurchaseRequisition`, `NormalizedQuote`, landed-cost, score, and supplier
  memory schemas;
- deterministic landed-cost calculation for freight, duties, lead-time delay,
  MOQ, payment-term financing, quality penalty, and explicit early-pay discount;
- a configurable, explained one-to-five-bid weighted comparison;
- local SQLite supplier-history repository with exact-decimal baselines;
- a generated Google ADK entry point with a deliberately read-only guardrail
  tool; and
- credential-free adversarial pytest coverage.

Not in this PR: PDF/CSV/email extraction, anomaly detection, negotiation,
persisted workflow states, SSE, the buyer UI, Expo companion, real email/ERP
integrations, or deployment infrastructure. Those are intentionally sequenced in
[the approved agent spec](.agents-cli-spec.md).

## Architecture

```text
untrusted quote input / future Gemini extraction
                   |
                   v
       strict Pydantic NormalizedQuote
                   |
                   v
 deterministic pricing + budget policy  <--- SQLite supplier memory
                   |
                   v
 explainable comparison matrix (no execution authority)
                   |
                   v
 future explicit HITL approval -> PO draft only
```

The model is not the policy authority. It may later propose structured extraction
or counter-offer prose, but validated deterministic Python remains responsible for
cost calculation, scoring, state transitions, confidentiality, and approval
checks.

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

No Gemini key is needed for domain policy and tests. When the ADK model surface is
exercised in a later milestone, copy `.env.example` to `.env` and configure a
local `GOOGLE_API_KEY` or `GEMINI_API_KEY`; never commit either file with a real
secret.

## agents-cli workflow

```bash
# inspect project metadata
agents-cli info

# run the future ADK evaluation loop after adding the negotiation adapter
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
| Confidentiality | Future counter-offers may use an anonymous benchmark but cannot disclose a competitor. |
| Execution | No SMTP, ERP, purchase, or PO-issuance tool exists in the agent surface. |

## Planned PR sequence

1. **Foundation and trust boundaries** — this PR.
2. **Stateful workflow and anomalies** — persistent runs/audit events, supplier
   baselines, and anomaly detector.
3. **Guarded Gemini negotiation + HITL API** — typed model adapter, FastAPI/SSE,
   and approval/PO-draft gate.
4. **Buyer experience** — Next.js comparison dashboard and Expo approval view.
5. **Evaluation, observability, deployability** — `agents-cli` datasets,
   adversarial simulator, container hardening, and deployment enhancement.

## Project layout

```text
app/
  agent.py                 # agents-cli scaffolded ADK boundary
  domain/models.py         # strict procurement contracts
  policies/pricing.py      # deterministic landed-cost and ranking policy
  persistence/sqlite.py    # durable local supplier memory
tests/unit/                # credential-free adversarial tests
.agents-cli-spec.md        # approved product/safety specification
agents-cli-manifest.yaml   # agents-cli scaffold metadata
```

## License

Apache-2.0. See [LICENSE](LICENSE).
