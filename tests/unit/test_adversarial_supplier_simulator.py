"""Synthetic suppliers exercise the same guards a live parser will face."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.models import NormalizedQuote
from app.policies.pricing import (
    PolicyViolation,
    PricingPolicy,
    calculate_landed_cost,
    compare_quotes,
)
from app.simulation.adversarial import AdversarialSupplierSimulator, SupplierTactic


def test_hidden_freight_cannot_be_normalized_as_a_free_quote(requisition) -> None:
    simulator = AdversarialSupplierSimulator(requisition)

    with pytest.raises(ValidationError, match="freight_cost"):
        NormalizedQuote.model_validate(simulator.raw_quote(SupplierTactic.HIDDEN_FREIGHT))


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
