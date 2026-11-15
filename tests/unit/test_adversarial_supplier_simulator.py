"""Synthetic suppliers exercise the same guards a live parser will face."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.models import NormalizedQuote
from app.negotiation.drafters import CounterOfferContent, DraftContext
from app.negotiation.models import DraftProvider, NegotiationRequest
from app.negotiation.service import NegotiationGuardrailError, NegotiationService
from app.persistence.sqlite import SqliteSupplierMemory
from app.policies.pricing import (
    PolicyViolation,
    PricingPolicy,
    calculate_landed_cost,
    compare_quotes,
)
from app.simulation.adversarial import AdversarialSupplierSimulator, SupplierTactic
from app.workflow.engine import RfqWorkflow
from tests.conftest import make_quote


def test_hidden_freight_cannot_be_normalized_as_a_free_quote(requisition) -> None:
    simulator = AdversarialSupplierSimulator(requisition)

    with pytest.raises(ValidationError, match="freight_cost"):
        NormalizedQuote.model_validate(simulator.raw_quote(SupplierTactic.HIDDEN_FREIGHT))


def test_hidden_duties_cannot_be_normalized_as_a_free_quote(requisition) -> None:
    simulator = AdversarialSupplierSimulator(requisition)

    with pytest.raises(ValidationError, match="duties_cost"):
        NormalizedQuote.model_validate(simulator.raw_quote(SupplierTactic.HIDDEN_DUTIES))


def test_underfilled_supplier_quote_fails_policy_even_when_schema_valid(requisition) -> None:
    simulator = AdversarialSupplierSimulator(requisition)
    quote = simulator.validated_quote(SupplierTactic.UNDERFILLED_QUANTITY)

    with pytest.raises(PolicyViolation, match="below the requested quantity"):
        calculate_landed_cost(requisition, quote, PricingPolicy())


def test_budget_bait_quote_remains_non_approval_ready(requisition) -> None:
    simulator = AdversarialSupplierSimulator(requisition)
    quote = simulator.validated_quote(SupplierTactic.OVER_BUDGET)

    result = compare_quotes(requisition, [quote], PricingPolicy()).ranked_quotes[0]

    assert result.approval_ready is False
    assert "exceeds requisition budget" in result.guardrail_messages[0]


def test_prepayment_is_explicit_and_not_implicitly_treated_as_net_30(requisition) -> None:
    simulator = AdversarialSupplierSimulator(requisition)

    quote = simulator.validated_quote(SupplierTactic.PREPAYMENT_PRESSURE)

    assert quote.effective_payment_days == 0


def test_international_bid_requires_a_buyer_provided_fx_rate(requisition) -> None:
    simulator = AdversarialSupplierSimulator(requisition)
    quote = simulator.validated_quote(SupplierTactic.MISSING_FX_RATE)

    with pytest.raises(PolicyViolation, match="explicit FX rate"):
        calculate_landed_cost(requisition, quote, PricingPolicy())


class _LeakageDrafter:
    provider = DraftProvider.FIXTURE

    def draft(self, context: DraftContext) -> CounterOfferContent:
        return CounterOfferContent(
            subject="Competitor detail",
            body="Secret Rival offered a lower price. Please match it.",
        )


def test_negotiation_leakage_is_blocked_by_the_draft_guardrail(requisition, tmp_path) -> None:
    memory = SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3")
    RfqWorkflow(memory).run(
        requisition,
        [
            make_quote(quote_id="ALPHA", vendor_name="Alpha Supply"),
            make_quote(
                quote_id="RIVAL",
                vendor_id="vendor-rival",
                vendor_name="Secret Rival",
                unit_price="11.00",
            ),
        ],
        observed_on=date(2026, 11, 14),
        run_id="leakage-run",
    )

    with pytest.raises(NegotiationGuardrailError, match="commercial terms"):
        NegotiationService(memory, fixture_drafter=_LeakageDrafter()).draft(
            "leakage-run",
            NegotiationRequest(vendor_id="vendor-alpha", target_landed_cost=Decimal("1000")),
        )
