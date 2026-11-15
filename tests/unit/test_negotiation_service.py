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


class _ObfuscatedCompetitorDrafter:
    provider = DraftProvider.FIXTURE

    def draft(self, context):  # type: ignore[no-untyped-def]
        return CounterOfferContent(
            subject="Commercial detail",
            body=(
                "\uff33\uff45\uff43\uff52\uff45\uff54\u2014\uff32\uff49\uff56\uff41\uff4c "
                "offered a better proposal. Please match it."
            ),
        )


class _TotalCostLeakyDrafter:
    provider = DraftProvider.FIXTURE

    def draft(self, context):  # type: ignore[no-untyped-def]
        return CounterOfferContent(
            subject="Commercial detail",
            body="Another proposal has a total landed cost of USD 1,250.00. Please beat it.",
        )


class _ObfuscatedExecutionClaimingDrafter:
    provider = DraftProvider.FIXTURE

    def draft(self, context):  # type: ignore[no-untyped-def]
        return CounterOfferContent(
            subject="Supplier update",
            body="We already dispatched the P.O.; your order is confirmed.",
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
    assert len(draft.thread) == 4
    assert [message.speaker.value for message in draft.thread] == [
        "buyer",
        "supplier",
        "buyer",
        "supplier",
    ]
    assert all(message.status == "simulated_draft_only" for message in draft.thread)
    assert memory.audit_events_for("negotiation-run")[-1].event_type == "counter_offer_drafted"
    persisted_drafts = memory.payload_for("negotiation-run")["counter_offer_drafts"]
    assert persisted_drafts["vendor-alpha"]["status"] == "draft_only"
    assert len(persisted_drafts["vendor-alpha"]["thread"]) == 4


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


def test_obfuscated_competitor_identity_is_blocked_before_returning_a_draft(
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
        run_id="obfuscated-rival-run",
    )

    with pytest.raises(NegotiationGuardrailError, match="commercial terms"):
        NegotiationService(memory, fixture_drafter=_ObfuscatedCompetitorDrafter()).draft(
            "obfuscated-rival-run",
            NegotiationRequest(vendor_id="vendor-alpha", target_landed_cost=Decimal("1000")),
        )

    assert all(
        event.event_type != "counter_offer_drafted"
        for event in memory.audit_events_for("obfuscated-rival-run")
    )


def test_competitor_total_landed_cost_is_blocked_before_returning_a_draft(
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
        run_id="total-cost-leak-run",
    )

    with pytest.raises(NegotiationGuardrailError, match="commercial terms"):
        NegotiationService(memory, fixture_drafter=_TotalCostLeakyDrafter()).draft(
            "total-cost-leak-run",
            NegotiationRequest(vendor_id="vendor-alpha", target_landed_cost=Decimal("1000")),
        )

    assert all(
        event.event_type != "counter_offer_drafted"
        for event in memory.audit_events_for("total-cost-leak-run")
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


def test_obfuscated_external_execution_claim_is_blocked_before_returning_a_draft(
    requisition, tmp_path
) -> None:
    memory = SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3")
    RfqWorkflow(memory).run(
        requisition,
        [make_quote(quote_id="ALPHA", vendor_name="Alpha Supply")],
        observed_on=date(2026, 11, 14),
        run_id="obfuscated-execution-claim-run",
    )

    with pytest.raises(NegotiationGuardrailError, match="external action"):
        NegotiationService(memory, fixture_drafter=_ObfuscatedExecutionClaimingDrafter()).draft(
            "obfuscated-execution-claim-run",
            NegotiationRequest(vendor_id="vendor-alpha", target_landed_cost=Decimal("1000")),
        )

    assert all(
        event.event_type != "counter_offer_drafted"
        for event in memory.audit_events_for("obfuscated-execution-claim-run")
    )
