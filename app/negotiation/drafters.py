"""Provider adapters that can propose, but never dispatch, counter-offer prose."""

from __future__ import annotations

import os
from typing import Protocol

from google import genai
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field

from app.negotiation.models import DraftProvider


class CounterOfferContent(BaseModel):
    """The restricted structured output allowed from a prose provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=4000)


class DraftContext(BaseModel):
    """Minimal, non-confidential material sent to a drafting provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    vendor_name: str
    requisition_id: str
    currency: str
    target_landed_cost: str


class CounterOfferDrafter(Protocol):
    """Swap a fixture implementation for Gemini without changing policy code."""

    provider: DraftProvider

    def draft(self, context: DraftContext) -> CounterOfferContent:
        """Return structured prose with no execution capability."""


class FixtureCounterOfferDrafter:
    """Deterministic local fallback used by tests and credential-free demos."""

    provider = DraftProvider.FIXTURE

    def draft(self, context: DraftContext) -> CounterOfferContent:
        return CounterOfferContent(
            subject=f"Request to revisit commercial terms for {context.requisition_id}",
            body=(
                f"Hello {context.vendor_name},\n\n"
                f"Thank you for your proposal for requisition {context.requisition_id}. "
                f"To move forward, we are seeking a landed-cost target at or below "
                f"{context.target_landed_cost} {context.currency}. Please review your unit pricing, "
                "freight, and payment terms and let us know whether you can improve the proposal."
                "\n\nRegards,\nProcurement Team"
            ),
        )


class GeminiCounterOfferDrafter:
    """Optional Gemini adapter; callers must opt in and configure a local key."""

    provider = DraftProvider.GEMINI

    def __init__(self, *, model_name: str | None = None) -> None:
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("Gemini drafting requires GOOGLE_API_KEY or GEMINI_API_KEY")
        self.model_name = model_name or os.getenv("MODEL_NAME", "gemini-3.8-flash")
        self.client = genai.Client(api_key=api_key)

    def draft(self, context: DraftContext) -> CounterOfferContent:
        prompt = f"""Draft a concise, polite procurement counter-offer email.

Recipient supplier: {context.vendor_name}
Requisition: {context.requisition_id}
Currency: {context.currency}
Approved anonymized target landed cost: {context.target_landed_cost}

Rules:
- Mention only the recipient supplier by name.
- Never mention another supplier, competitor, quotation, attachment, or contact.
- Do not claim that this email was sent; it is a draft for buyer review only.
- Ask to revisit unit price, freight, and payment terms.
Return JSON matching the requested schema."""
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
                response_schema=CounterOfferContent,
            ),
        )
        if response.parsed is None:
            raise ValueError("Gemini returned no structured counter-offer draft")
        return CounterOfferContent.model_validate(response.parsed)
