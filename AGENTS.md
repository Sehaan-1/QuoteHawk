# QuoteHawk — Coding Agent Guide

## Product contract

Read [`.agents-cli-spec.md`](.agents-cli-spec.md) before changing agent behavior.
QuoteHawk is a **draft-only procurement assistant**. It must never send vendor
email, access an ERP, place an order, issue a binding PO, leak a competitor's
identity or terms, or bypass buyer approval.

## Architecture authority

- `app/domain/` contains strict Pydantic contracts for all procurement input.
- `app/policies/` is deterministic policy authority for cost math, budget rules,
  scoring, and future workflow transitions. Do not move this authority into an
  LLM prompt.
- `app/persistence/` provides supplier-memory interfaces and the local SQLite
  implementation. Keep it replaceable by Postgres.
- `app/agent.py` is the optional Google ADK/Gemini boundary. Treat all model
  output as untrusted until Pydantic validates it.

Use `Decimal` for money; do not introduce binary floating-point pricing.

## agents-cli lifecycle

This project was scaffolded with `google-agents-cli` 1.14.0 using the ADK Python
prototype template. Use its lifecycle deliberately:

1. Update and obtain approval for `.agents-cli-spec.md` when requirements or
   safety constraints change.
2. Before a new agent capability, read the relevant `agents-cli` skill/recipe.
3. Implement deterministic unit coverage first, then model/evaluation coverage.
4. Use `agents-cli eval run` after an LLM negotiation/extraction surface exists.
5. Do not deploy or add cloud resources without explicit human approval.

Useful commands:

```bash
uv sync --all-groups
uv run pytest
uv run ruff check .
agents-cli info
```

## Coding rules

- Keep each source file below 1,000 lines.
- Reject missing commercial values rather than defaulting them to zero.
- Retain structured, audit-friendly explanations for every policy result.
- Do not expose hidden model reasoning; surface concise business calculations.
- Preserve the generated A2A/FastAPI plumbing unless a later approved PR needs
  to change it. Avoid hand-writing A2A integration.
- Do not change the configured Gemini model without explicit user approval.
- Keep all credentials in ignored `.env`; `.env.example` may contain names only.

## Tests

The core tests must be network- and credential-free. Add adversarial cases for
hidden fees, invalid schema data, price/budget exceptions, confidentiality, and
approval-bypass attempts as those capabilities are introduced.
