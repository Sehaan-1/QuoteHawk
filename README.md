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

## Current milestone — PR 3

The prototype now exposes a controlled API and buyer gate around the deterministic
engine:

- fixture JSON and optional Gemini structured quote extraction, both revalidated
  as `NormalizedQuote` before policy code receives them;
- fixture and optional Gemini counter-offer drafting that receives only recipient
  context plus an anonymized target, then blocks competitor-name disclosure;
- FastAPI routes for RFQ runs, fixture extraction, counter-offer drafts, buyer
  approval, durable run detail, and server-sent audit events;
- a persisted buyer-approval decision that accepts only an allowlisted local
  prototype identity and a within-budget selected quote; and
- a non-binding, persisted PO **draft** after approval—never an ERP write-back,
  email, purchase, or executable purchase order.

The local buyer allowlist is intentionally not production authentication. Replace
it with verified identity/OAuth before deployment. The web/mobile buyer experience,
real document ingestion, real vendor email, ERP execution, and deployment
infrastructure remain intentionally out of scope for this prototype stage.

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
 allowlisted buyer decision -> non-binding PO draft only
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
secret. `QUOTEHAWK_AUTHORIZED_BUYERS` is a credential-free local allowlist for
the prototype—not a replacement for production authentication.

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
| Approval | Only an allowlisted buyer can decide an awaiting request, and only an eligible quote can yield a draft. |
| Execution | No SMTP, ERP, purchase, or PO-issuance tool exists in the agent surface; an approved payload is `draft_only`. |

## Planned PR sequence

1. **Foundation and trust boundaries** — complete.
2. **Stateful workflow and anomalies** — complete.
3. **Guarded Gemini negotiation + HITL API** — complete.
4. **Buyer experience** — Next.js comparison dashboard and Expo approval view.
5. **Evaluation, observability, deployability** — `agents-cli` datasets,
   broader adversarial simulator coverage, container hardening, and deployment enhancement.

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
tests/unit/                # credential-free policy, API, approval, and workflow tests
.agents-cli-spec.md        # approved product/safety specification
agents-cli-manifest.yaml   # agents-cli scaffold metadata
```

## License

Apache-2.0. See [LICENSE](LICENSE).
