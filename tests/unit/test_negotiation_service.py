"""Counter-offers must remain confidential, draft-only, and fixture-runnable."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.negotiation.drafters import CounterOfferContent
from app.negotiation.models import DraftProvider, NegotiationRequest
from app.negotiation.service import NegotiationGuardrailError, NegotiationService
from app.persistence.sqlite import SqliteSupplierMemory
from app.workflow.engine import RfqWorkflow
from tests.conftest import make_quote


class _LeakyDrafter:
    provider = DraftProvider.FIXTURE

    def draft(self, context):  # type: ignore[no-untyped-def]
        return CounterOfferContent(
            subject="Competitor detail",
            body="Secret Rival quoted a better price. Please match it.",
        )


class _TermLeakyDrafter:
    provider = DraftProvider.FIXTURE

    def draft(self, context):  # type: ignore[no-untyped-def]
        return CounterOfferContent(
            subject="Commercial detail",
            body="Please beat the 11.00 unit price in another supplier proposal.",
        )


class _ExecutionClaimingDrafter:
    provider = DraftProvider.FIXTURE

    def draft(self, context):  # type: ignore[no-untyped-def]
        return CounterOfferContent(
            subject="Supplier update",
            body="This email has been sent and the purchase order was issued.",
        )


def test_fixture_counter_offer_is_draft_only_and_hides_competitor(requisition, tmp_path) -> None:
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
        run_id="negotiation-run",
    )

    draft = NegotiationService(memory).draft(
        "negotiation-run",
        NegotiationRequest(
            vendor_id="vendor-alpha",
            target_landed_cost=Decimal("1000"),
            provider=DraftProvider.FIXTURE,
        ),
    )

    assert draft.status == "draft_only"
    assert draft.provider is DraftProvider.FIXTURE
    assert "Secret Rival" not in draft.body
    assert memory.audit_events_for("negotiation-run")[-1].event_type == "counter_offer_drafted"


def test_competitor_name_from_a_provider_is_blocked_before_returning_a_draft(
    requisition, tmp_path
) -> None:
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
        run_id="leaky-negotiation-run",
    )

    with pytest.raises(NegotiationGuardrailError, match="commercial terms"):
        NegotiationService(memory, fixture_drafter=_LeakyDrafter()).draft(
            "leaky-negotiation-run",
            NegotiationRequest(vendor_id="vendor-alpha", target_landed_cost=Decimal("1000")),
        )


def test_competitor_quote_term_is_blocked_before_returning_a_draft(requisition, tmp_path) -> None:
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
        run_id="term-leak-run",
    )

    with pytest.raises(NegotiationGuardrailError, match="commercial terms"):
        NegotiationService(memory, fixture_drafter=_TermLeakyDrafter()).draft(
            "term-leak-run",
            NegotiationRequest(vendor_id="vendor-alpha", target_landed_cost=Decimal("1000")),
        )


def test_provider_claiming_external_execution_is_blocked_before_returning_a_draft(
    requisition, tmp_path
) -> None:
    memory = SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3")
    RfqWorkflow(memory).run(
        requisition,
        [make_quote(quote_id="ALPHA", vendor_name="Alpha Supply")],
        observed_on=date(2026, 11, 14),
        run_id="execution-claim-run",
    )

    with pytest.raises(NegotiationGuardrailError, match="external action"):
        NegotiationService(memory, fixture_drafter=_ExecutionClaimingDrafter()).draft(
            "execution-claim-run",
            NegotiationRequest(vendor_id="vendor-alpha", target_landed_cost=Decimal("1000")),
        )

    assert all(
        event.event_type != "counter_offer_drafted"
        for event in memory.audit_events_for("execution-claim-run")
    )
