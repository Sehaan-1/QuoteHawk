"""Rolling supplier baselines must expose cost leakage deterministically."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.analysis.anomaly_detector import AnomalyPolicy, detect_quote_anomalies
from app.analysis.models import AnomalyMetric
from app.persistence.sqlite import SqliteSupplierMemory
from tests.conftest import make_quote


def test_freight_22_percent_above_six_month_baseline_is_flagged(tmp_path) -> None:
    memory = SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3")
    memory.record_quote_history(
        make_quote(quote_id="HIST-QUOTE-1", freight_cost="100"), date(2026, 6, 1)
    )
    memory.record_quote_history(
        make_quote(quote_id="HIST-QUOTE-2", freight_cost="100"), date(2026, 8, 1)
    )
    current_date = date(2026, 11, 14)
    baseline = memory.quote_baseline_for(
        "vendor-alpha", "USD", as_of=current_date, lookback_days=180
    )
    current_quote = make_quote(quote_id="CURRENT", freight_cost="122")

    anomalies = detect_quote_anomalies(current_quote, baseline, AnomalyPolicy(), current_date)

    freight_anomaly = next(
        anomaly for anomaly in anomalies if anomaly.metric is AnomalyMetric.FREIGHT_COST
    )
    assert freight_anomaly.baseline_value == Decimal("100")
    assert freight_anomaly.observed_value == Decimal("122")
    assert freight_anomaly.deviation_rate == Decimal("0.22")
    assert "22.0000% above" in freight_anomaly.explanation


def test_exact_threshold_and_thin_history_do_not_create_false_positive(tmp_path) -> None:
    memory = SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3")
    memory.record_quote_history(
        make_quote(quote_id="HIST-QUOTE-1", freight_cost="100"), date(2026, 6, 1)
    )
    memory.record_quote_history(
        make_quote(quote_id="HIST-QUOTE-2", freight_cost="100"), date(2026, 8, 1)
    )
    current_date = date(2026, 11, 14)
    baseline = memory.quote_baseline_for(
        "vendor-alpha", "USD", as_of=current_date, lookback_days=180
    )

    exact_threshold = make_quote(quote_id="CURRENT", freight_cost="120")
    assert detect_quote_anomalies(exact_threshold, baseline, AnomalyPolicy(), current_date) == ()
    assert (
        detect_quote_anomalies(
            make_quote(quote_id="THIN", freight_cost="160"),
            baseline,
            AnomalyPolicy(minimum_history=3),
            current_date,
        )
        == ()
    )
