"""Guarded counter-offer generation over persisted RFQ data."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal

from app.domain.models import NormalizedQuote, QuoteComparison
from app.negotiation.drafters import (
    CounterOfferContent,
    CounterOfferDrafter,
    DraftContext,
    FixtureCounterOfferDrafter,
    GeminiCounterOfferDrafter,
)
from app.negotiation.models import CounterOfferDraft, DraftProvider, NegotiationRequest
from app.persistence.sqlite import SqliteSupplierMemory
from app.workflow.models import RfqRunState

_EXECUTION_CLAIM_PATTERNS = (
    re.compile(r"\b(?:has been|was)\s+(?:sent|emailed|dispatched)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:purchase order|po)\s+(?:has been|was)\s+(?:issued|created|submitted|sent)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:we|i)\s+(?:have|has)\s+(?:issued|placed|submitted)\b", re.IGNORECASE),
)


class NegotiationGuardrailError(ValueError):
    """Raised when a draft request risks confidential or invalid negotiation."""


class NegotiationService:
    """Generate reviewable drafts using an anonymous target, never a competitor quote."""

    def __init__(
        self,
        memory: SqliteSupplierMemory,
        *,
        fixture_drafter: CounterOfferDrafter | None = None,
        gemini_drafter: CounterOfferDrafter | None = None,
    ) -> None:
        self.memory = memory
        self.fixture_drafter = fixture_drafter or FixtureCounterOfferDrafter()
        self.gemini_drafter = gemini_drafter

    def draft(self, run_id: str, request: NegotiationRequest) -> CounterOfferDraft:
        """Build a counter-offer only for a completed, non-approved RFQ run."""

        run = self.memory.load_rfq_run(run_id)
        if run.state is not RfqRunState.NEGOTIATION_OR_READY:
            raise NegotiationGuardrailError(
                "counter-offers can only be drafted before the buyer approval gate opens"
            )

        payload = self.memory.payload_for(run_id)
        quotes = tuple(NormalizedQuote.model_validate(item) for item in payload["quotes"])
        current_quote = next(
            (quote for quote in quotes if quote.vendor_id == request.vendor_id), None
        )
        if current_quote is None:
            raise NegotiationGuardrailError("vendor does not belong to this RFQ run")
        comparison = QuoteComparison.model_validate(payload["comparison"])
        current_score = next(
            (
                score
                for score in comparison.ranked_quotes
                if score.quote_id == current_quote.quote_id
            ),
            None,
        )
        if current_score is None:
            raise NegotiationGuardrailError("vendor quote has no completed comparison")
        current_landed_cost = current_score.landed_cost.total_landed_cost
        if request.target_landed_cost >= current_landed_cost:
            raise NegotiationGuardrailError(
                "counter-offer target must be lower than current landed cost"
            )

        context = DraftContext(
            vendor_name=current_quote.vendor_name,
            requisition_id=run.requisition_id,
            currency=current_quote.currency,
            target_landed_cost=str(request.target_landed_cost),
        )
        drafter = self._drafter_for(request.provider)
        content = drafter.draft(context)
        self._assert_no_competitor_disclosure(content, current_quote.vendor_name, quotes)
        self._assert_draft_only_language(content)

        draft = CounterOfferDraft(
            run_id=run_id,
            vendor_id=current_quote.vendor_id,
            subject=content.subject,
            body=content.body,
            target_landed_cost=Decimal(request.target_landed_cost),
            currency=current_quote.currency,
            provider=request.provider,
            created_at=datetime.now(UTC),
        )
        self.memory.record_rfq_event(
            run_id=run_id,
            event_type="counter_offer_drafted",
            reason="A confidentiality-checked counter-offer draft was prepared for buyer review.",
            details={
                "vendor_id": current_quote.vendor_id,
                "provider": request.provider.value,
                "execution_boundary": "draft_only",
            },
        )
        return draft

    def _drafter_for(self, provider: DraftProvider) -> CounterOfferDrafter:
        if provider is DraftProvider.FIXTURE:
            return self.fixture_drafter
        if self.gemini_drafter is None:
            self.gemini_drafter = GeminiCounterOfferDrafter()
        return self.gemini_drafter

    @staticmethod
    def _assert_no_competitor_disclosure(
        content: CounterOfferContent,
        recipient_name: str,
        quotes: tuple[NormalizedQuote, ...],
    ) -> None:
        """Block names, identifiers, and exact commercial terms from other bids."""

        message = f"{content.subject}\n{content.body}".casefold()
        competitor_values: set[str] = set()
        for quote in quotes:
            if quote.vendor_name.casefold() == recipient_name.casefold():
                continue
            competitor_values.update(
                {
                    quote.vendor_id.casefold(),
                    quote.vendor_name.casefold(),
                    quote.quote_id.casefold(),
                    str(quote.freight_cost).casefold(),
                    str(quote.duties_cost).casefold(),
                    str(quote.lead_time_days).casefold(),
                    quote.payment_terms.value.casefold(),
                }
            )
            competitor_values.update(str(line.unit_price).casefold() for line in quote.lines)

        disclosed = next((value for value in competitor_values if value and value in message), None)
        if disclosed is not None:
            raise NegotiationGuardrailError(
                "counter-offer draft contains competing supplier identity or commercial terms and was blocked"
            )

    @staticmethod
    def _assert_draft_only_language(content: CounterOfferContent) -> None:
        """Block prose that falsely claims an email or binding order was executed."""

        message = f"{content.subject}\n{content.body}"
        if any(pattern.search(message) for pattern in _EXECUTION_CLAIM_PATTERNS):
            raise NegotiationGuardrailError(
                "counter-offer draft claims an external action was executed and was blocked"
            )
