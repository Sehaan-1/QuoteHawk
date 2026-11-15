"""FX inputs are explicit, durable, and used by the deterministic workflow."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.domain.models import FxRate
from app.persistence.sqlite import SqliteSupplierMemory
from app.workflow.engine import RfqWorkflow
from tests.conftest import make_quote


def test_workflow_persists_explicit_fx_rate_and_scores_in_pr_currency(
    requisition, tmp_path
) -> None:
    memory = SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3")

    result = RfqWorkflow(memory).run(
        requisition,
        [make_quote(quote_id="EUR-1", vendor_id="vendor-eur", currency="EUR")],
        fx_rates=(FxRate(source_currency="EUR", target_currency="USD", rate=Decimal("1.10")),),
        observed_on=date(2026, 11, 14),
        run_id="fx-run",
    )

    score = result.comparison.ranked_quotes[0]
    snapshot = memory.payload_for("fx-run")
    assert score.landed_cost.currency == "USD"
    assert score.landed_cost.source_currency == "EUR"
    assert score.landed_cost.fx_rate == Decimal("1.10")
    assert snapshot["fx_rates"] == [
        {
            "source_currency": "EUR",
            "target_currency": "USD",
            "rate": "1.10",
            "as_of": None,
            "source": "buyer_supplied",
        }
    ]
