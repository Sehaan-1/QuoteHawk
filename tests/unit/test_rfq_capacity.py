"""RFQ volume remains bounded but no longer stops at five suppliers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.persistence.sqlite import SqliteSupplierMemory
from app.workflow.engine import RfqWorkflow
from tests.conftest import make_quote


def test_workflow_persists_and_ranks_twenty_supplier_bids(requisition, tmp_path) -> None:
    memory = SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3")
    quotes = [
        make_quote(
            quote_id=f"BID-{index:02d}",
            vendor_id=f"supplier-{index:02d}",
            vendor_name=f"Supplier {index:02d}",
            unit_price=f"{Decimal('9.00') + Decimal(index) / Decimal('10'):.2f}",
        )
        for index in range(20)
    ]

    result = RfqWorkflow(memory).run(
        requisition,
        quotes,
        observed_on=date(2026, 11, 14),
        run_id="twenty-supplier-run",
    )

    assert len(result.comparison.ranked_quotes) == 20
    assert len(memory.payload_for("twenty-supplier-run")["quotes"]) == 20
