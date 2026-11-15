"""Guarded counter-offer generation over persisted RFQ data."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from decimal import Decimal

from app.auth.models import DEFAULT_TENANT_ID
from app.domain.models import (
    NormalizedQuote,
    PaymentTerms,
    QuoteComparison,
    assert_unique_quote_identities,
    persisted_quote_from_payload,
)
from app.negotiation.drafters import (
    CounterOfferContent,
    CounterOfferDrafter,
    DraftContext,
    FixtureCounterOfferDrafter,
    GeminiCounterOfferDrafter,
)
from app.negotiation.models import (
    CounterOfferDraft,
    DraftProvider,
    NegotiationRequest,
    NegotiationSpeaker,
    NegotiationThreadMessage,
)
from app.persistence.repository import QuoteHawkRepository
from app.workflow.models import RfqRunState

_EXECUTION_CLAIM_PATTERNS = (
    re.compile(r"\b(?:has been|was|is now|already)\s+(?:sent|emailed|dispatched)\b"),
    re.compile(
        r"\b(?:purchase\s+order|p\.?\s*o\.?)\s+(?:has been|was|is now|already)\s+"
        r"(?:issued|created|submitted|sent|placed)\b"
    ),
    re.compile(
        r"\b(?:we|i)\s+(?:(?:have|has)\s+|already\s+)?"
        r"(?:issued|placed|submitted|sent|emailed|dispatched)\b"
    ),
    re.compile(r"\b(?:order|purchase)\s+(?:is|was|has been)\s+(?:confirmed|placed|processed)\b"),
)
_THOUSANDS_SEPARATOR_PATTERN = re.compile(r"(?<=\d),(?=\d{3}(?:\D|$))")
_DECIMAL_COMMA_PATTERN = re.compile(r"(?<=\d),(?=\d)")
_CURRENCY_AMOUNT_PATTERN = r"(?:[$€₹]\s*|\b(?:usd|eur|inr)\s+){amount}(?!\d)"
_TEXTUAL_AMOUNT_PATTERN = r"(?<!\d){amount}\s+(?:usd|eur|inr|dollars?|euros?|rupees?)\b"


def _normalize_disclosure_text(value: str) -> str:
    """Normalize harmless presentation variants before deterministic matching."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = _THOUSANDS_SEPARATOR_PATTERN.sub("", normalized)
    return _DECIMAL_COMMA_PATTERN.sub(".", normalized)


def _compact_disclosure_text(value: str) -> str:
    """Remove punctuation and spacing so simple visual obfuscation cannot bypass a block."""

    return "".join(character for character in value if character.isalnum())


def _is_obfuscated_identity_in_message(identity: str, compact_message: str) -> bool:
    compact_identity = _compact_disclosure_text(_normalize_disclosure_text(identity))
    # Short IDs such as "A" are too collision-prone to safely substring-match.
    return len(compact_identity) >= 4 and compact_identity in compact_message


def _is_commercial_value_in_message(value: Decimal, message: str) -> bool:
    """Match exact quoted amounts across common currency and number formatting."""

    if value == 0:
        return False
    canonical = format(value, "f")
    normalized_amount = canonical.rstrip("0").rstrip(".") if "." in canonical else canonical
    exact_pattern = re.compile(rf"(?<!\d){re.escape(canonical)}(?!\d)")
    if exact_pattern.search(message):
        return True
    currency_pattern = re.compile(
        _CURRENCY_AMOUNT_PATTERN.format(amount=re.escape(normalized_amount))
    )
    textual_pattern = re.compile(
        _TEXTUAL_AMOUNT_PATTERN.format(amount=re.escape(normalized_amount))
    )
    return bool(currency_pattern.search(message) or textual_pattern.search(message))


def _is_payment_or_lead_term_in_message(
    quote: NormalizedQuote,
    compact_message: str,
    message: str,
) -> bool:
    payment_term = _compact_disclosure_text(quote.payment_terms.value)
    if payment_term in compact_message:
        return True
    if quote.payment_terms is PaymentTerms.LETTER_OF_CREDIT and "letterofcredit" in compact_message:
        return True
    if quote.payment_terms is PaymentTerms.MILESTONE and "milestone" in compact_message:
        return True
    return bool(
        re.search(
            rf"(?<!\d){quote.lead_time_days}\s*(?:business\s*)?(?:days?|d)\b",
            message,
        )
    )


class NegotiationGuardrailError(ValueError):
    """Raised when a draft request risks confidential or invalid negotiation."""


class NegotiationService:
    """Generate reviewable drafts using an anonymous target, never a competitor quote."""

    def __init__(
        self,
        memory: QuoteHawkRepository,
        *,
        fixture_drafter: CounterOfferDrafter | None = None,
        gemini_drafter: CounterOfferDrafter | None = None,
        max_target_reduction_rate: Decimal = Decimal("0.15"),
    ) -> None:
        if not Decimal("0") < max_target_reduction_rate <= Decimal("1"):
            raise ValueError("max_target_reduction_rate must be between zero and one")
        self.memory = memory
        self.fixture_drafter = fixture_drafter or FixtureCounterOfferDrafter()
        self.gemini_drafter = gemini_drafter
        self.max_target_reduction_rate = max_target_reduction_rate

    def draft(
        self,
        run_id: str,
        request: NegotiationRequest,
        *,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> CounterOfferDraft:
        """Build a counter-offer only for a completed, non-approved RFQ run."""

        run = self.memory.load_rfq_run(run_id, tenant_id=tenant_id)
        if run.state is not RfqRunState.NEGOTIATION_OR_READY:
            raise NegotiationGuardrailError(
                "counter-offers can only be drafted before the buyer approval gate opens"
            )

        payload = self.memory.payload_for(run_id, tenant_id=tenant_id)
        quotes = tuple(persisted_quote_from_payload(item) for item in payload["quotes"])
        try:
            assert_unique_quote_identities(quotes)
        except ValueError as error:
            raise NegotiationGuardrailError(
                "RFQ run contains ambiguous supplier bid identities"
            ) from error
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
        reduction_rate = (current_landed_cost - request.target_landed_cost) / current_landed_cost
        if reduction_rate > self.max_target_reduction_rate:
            raise NegotiationGuardrailError(
                "counter-offer target exceeds the configured negotiation threshold"
            )

        context = DraftContext(
            vendor_name=current_quote.vendor_name,
            requisition_id=run.requisition_id,
            currency=current_quote.currency,
            target_landed_cost=str(request.target_landed_cost),
        )
        drafter = self._drafter_for(request.provider)
        content = drafter.draft(context)
        self._assert_no_competitor_disclosure(
            content,
            recipient_vendor_id=current_quote.vendor_id,
            quotes=quotes,
            comparison=comparison,
        )
        self._assert_draft_only_language(content)
        thread = self._simulate_thread(
            content,
            rounds=request.simulated_rounds,
            requisition_id=run.requisition_id,
        )

        draft = CounterOfferDraft(
            run_id=run_id,
            vendor_id=current_quote.vendor_id,
            subject=content.subject,
            body=content.body,
            target_landed_cost=Decimal(request.target_landed_cost),
            currency=current_quote.currency,
            provider=request.provider,
            created_at=datetime.now(UTC),
            thread=thread,
        )
        self.memory.store_counter_offer_draft(draft=draft, tenant_id=tenant_id)
        return draft

    @staticmethod
    def _simulate_thread(
        content: CounterOfferContent,
        *,
        rounds: int,
        requisition_id: str,
    ) -> tuple[NegotiationThreadMessage, ...]:
        """Create a clearly simulated review thread without a messaging capability."""

        messages: list[NegotiationThreadMessage] = []
        for round_number in range(1, rounds + 1):
            buyer_body = (
                content.body
                if round_number == 1
                else (
                    f"Hello,\n\nFollowing up on the draft-only discussion for {requisition_id}. "
                    "Please confirm whether the requested commercial revision can be considered. "
                    "This is a simulation for buyer review; no message was sent.\n\nRegards,\nProcurement Team"
                )
            )
            messages.append(
                NegotiationThreadMessage(
                    round_number=round_number,
                    speaker=NegotiationSpeaker.BUYER,
                    subject=content.subject,
                    body=buyer_body,
                )
            )
            messages.append(
                NegotiationThreadMessage(
                    round_number=round_number,
                    speaker=NegotiationSpeaker.SUPPLIER,
                    subject=f"Re: {content.subject}",
                    body=(
                        "Simulated supplier reply for buyer review only. The supplier may review "
                        "the requested commercial revision and provide a revised quotation. "
                        "No supplier email was delivered."
                    ),
                )
            )
        return tuple(messages)

    def _drafter_for(self, provider: DraftProvider) -> CounterOfferDrafter:
        if provider is DraftProvider.FIXTURE:
            return self.fixture_drafter
        if self.gemini_drafter is None:
            self.gemini_drafter = GeminiCounterOfferDrafter()
        return self.gemini_drafter

    @staticmethod
    def _assert_no_competitor_disclosure(
        content: CounterOfferContent,
        *,
        recipient_vendor_id: str,
        quotes: tuple[NormalizedQuote, ...],
        comparison: QuoteComparison,
    ) -> None:
        """Block direct and lightly obfuscated rival identities or commercial terms."""

        message = _normalize_disclosure_text(f"{content.subject}\n{content.body}")
        compact_message = _compact_disclosure_text(message)
        scores_by_quote_id = {score.quote_id: score for score in comparison.ranked_quotes}
        for quote in quotes:
            if quote.vendor_id == recipient_vendor_id:
                continue
            competitor_score = scores_by_quote_id.get(quote.quote_id)
            identities = (quote.vendor_id, quote.vendor_name, quote.quote_id)
            if any(
                _is_obfuscated_identity_in_message(identity, compact_message)
                for identity in identities
            ):
                raise NegotiationGuardrailError(
                    "counter-offer draft contains competing supplier identity or commercial terms and was blocked"
                )
            commercial_values = [
                quote.freight_cost,
                quote.shipping_cost,
                quote.duties_cost,
                *(line.unit_price for line in quote.lines),
            ]
            if competitor_score is not None:
                commercial_values.append(competitor_score.landed_cost.total_landed_cost)
            if any(_is_commercial_value_in_message(value, message) for value in commercial_values):
                raise NegotiationGuardrailError(
                    "counter-offer draft contains competing supplier identity or commercial terms and was blocked"
                )
            if _is_payment_or_lead_term_in_message(quote, compact_message, message):
                raise NegotiationGuardrailError(
                    "counter-offer draft contains competing supplier identity or commercial terms and was blocked"
                )

    @staticmethod
    def _assert_draft_only_language(content: CounterOfferContent) -> None:
        """Block prose that falsely claims an email or binding order was executed."""

        message = _normalize_disclosure_text(f"{content.subject}\n{content.body}")
        if any(pattern.search(message) for pattern in _EXECUTION_CLAIM_PATTERNS):
            raise NegotiationGuardrailError(
                "counter-offer draft claims an external action was executed and was blocked"
            )
