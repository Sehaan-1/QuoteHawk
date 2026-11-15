"""Thresholded multi-turn negotiation remains draft-only and confidentiality-safe."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.negotiation.models import NegotiationRequest
from app.negotiation.service import NegotiationGuardrailError, NegotiationService
from app.persistence.sqlite import SqliteSupplierMemory
from app.workflow.engine import RfqWorkflow
from tests.conftest import make_quote


def test_negotiation_rejects_a_target_below_the_configured_threshold(requisition, tmp_path) -> None:
    memory = SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3")
    RfqWorkflow(memory).run(
        requisition,
        [make_quote(quote_id="ALPHA", vendor_name="Alpha Supply")],
        observed_on=date(2026, 11, 14),
        run_id="threshold-run",
    )

    with pytest.raises(NegotiationGuardrailError, match="configured negotiation threshold"):
        NegotiationService(memory, max_target_reduction_rate=Decimal("0.10")).draft(
            "threshold-run",
            NegotiationRequest(vendor_id="vendor-alpha", target_landed_cost=Decimal("1000")),
        )


def test_negotiation_can_simulate_up_to_five_draft_only_rounds(requisition, tmp_path) -> None:
    memory = SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3")
    RfqWorkflow(memory).run(
        requisition,
        [make_quote(quote_id="ALPHA", vendor_name="Alpha Supply")],
        observed_on=date(2026, 11, 14),
        run_id="thread-run",
    )

    draft = NegotiationService(memory).draft(
        "thread-run",
        NegotiationRequest(
            vendor_id="vendor-alpha",
            target_landed_cost=Decimal("1100"),
            simulated_rounds=3,
        ),
    )

    assert len(draft.thread) == 6
    assert {message.round_number for message in draft.thread} == {1, 2, 3}
    assert all("No supplier email was delivered" in message.body for message in draft.thread[1::2])
