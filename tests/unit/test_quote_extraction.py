"""Fixture extraction uses the exact Pydantic boundary a Gemini adapter must pass."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.ingestion.extractors import FixtureQuoteExtractor
from app.simulation.adversarial import AdversarialSupplierSimulator, SupplierTactic


def test_fixture_extractor_returns_strict_normalized_quote(requisition) -> None:
    raw_quote = json.dumps(
        AdversarialSupplierSimulator(requisition).raw_quote(SupplierTactic.FREIGHT_SPIKE)
    )

    quote = FixtureQuoteExtractor().extract(raw_quote)

    assert quote.quote_id == "SIM-1"
    assert str(quote.freight_cost) == "122.00"


def test_fixture_extractor_rejects_omitted_commercial_terms(requisition) -> None:
    raw_quote = json.dumps(
        AdversarialSupplierSimulator(requisition).raw_quote(SupplierTactic.HIDDEN_FREIGHT)
    )

    with pytest.raises(ValidationError, match="freight_cost"):
        FixtureQuoteExtractor().extract(raw_quote)
