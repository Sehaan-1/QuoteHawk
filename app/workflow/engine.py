"""Deterministic RFQ workflow with durable state, snapshots, and audit events."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.analysis.anomaly_detector import AnomalyPolicy, detect_quote_anomalies
from app.analysis.models import QuoteAnomaly, SupplierQuoteBaseline
from app.domain.models import (
    NormalizedQuote,
    PurchaseRequisition,
    SupplierBaseline,
)
from app.persistence.sqlite import SqliteSupplierMemory
from app.policies.pricing import PricingPolicy, compare_quotes
from app.workflow.models import RfqRunState, RfqWorkflowResult


class RfqWorkflow:
    """Run the policy-first PR-2 path without any model or external side effect.

    The database is the source of truth for every transition. A future API/ADK
    surface can resume from the saved payload, but no code path here can issue a
    PO or change the state to approved.
    """

    def __init__(
        self,
        memory: SqliteSupplierMemory,
        *,
        pricing_policy: PricingPolicy | None = None,
        anomaly_policy: AnomalyPolicy | None = None,
    ) -> None:
        self.memory = memory
        self.pricing_policy = pricing_policy or PricingPolicy()
        self.anomaly_policy = anomaly_policy or AnomalyPolicy()

    def run(
        self,
        requisition: PurchaseRequisition,
        quotes: Iterable[NormalizedQuote],
        *,
        observed_on: date | None = None,
        run_id: str | None = None,
    ) -> RfqWorkflowResult:
        """Normalize the persisted flow from intake through negotiation readiness."""

        received_quotes = tuple(quotes)
        run_id = run_id or f"rfq-{uuid4()}"
        observed_on = observed_on or date.today()
        self.memory.create_rfq_run(
            run_id=run_id,
            requisition_id=requisition.requisition_id,
            payload={
                "requisition": requisition.model_dump(mode="json"),
                "quotes": [quote.model_dump(mode="json") for quote in received_quotes],
                "observed_on": observed_on.isoformat(),
            },
        )

        try:
            self.memory.advance_rfq_run(
                run_id=run_id,
                to_state=RfqRunState.NORMALIZED,
                event_type="quotes_normalized",
                reason="Supplier quotes passed strict normalized-quote validation.",
                details={"quote_count": len(received_quotes)},
            )

            memory_payload, supplier_performance, quote_baselines = self._load_memory(
                received_quotes, observed_on
            )
            self.memory.advance_rfq_run(
                run_id=run_id,
                to_state=RfqRunState.ENRICHED_WITH_MEMORY,
                event_type="supplier_memory_loaded",
                reason="Historical supplier baselines loaded before current bids were stored.",
                details={"vendors_with_history": len(memory_payload)},
                payload_patch={"supplier_memory": memory_payload},
            )

            comparison = compare_quotes(
                requisition,
                received_quotes,
                self.pricing_policy,
                supplier_performance=supplier_performance,
            )
            anomalies = self._detect_anomalies(received_quotes, quote_baselines, observed_on)
            self.memory.advance_rfq_run(
                run_id=run_id,
                to_state=RfqRunState.ANALYZED,
                event_type="quotes_analyzed",
                reason="Landed costs, weighted comparison, and supplier-history anomalies calculated.",
                details={
                    "anomaly_count": len(anomalies),
                    "approval_ready_quote_count": sum(
                        score.approval_ready for score in comparison.ranked_quotes
                    ),
                },
                payload_patch={
                    "comparison": comparison.model_dump(mode="json"),
                    "anomalies": [anomaly.model_dump(mode="json") for anomaly in anomalies],
                },
            )

            # Persist only after using the prior window, preventing self-baselining.
            for quote in received_quotes:
                self.memory.record_quote_history(quote, observed_on)

            approval_ready_count = sum(score.approval_ready for score in comparison.ranked_quotes)
            reason = (
                "At least one bid is within budget; PR 3 will expose a buyer approval gate."
                if approval_ready_count
                else "No bid is within budget; a future negotiation path is required."
            )
            run = self.memory.advance_rfq_run(
                run_id=run_id,
                to_state=RfqRunState.NEGOTIATION_OR_READY,
                event_type="negotiation_or_readiness_determined",
                reason=reason,
                details={
                    "approval_ready_quote_count": approval_ready_count,
                    "execution_boundary": "draft_only",
                },
            )
        except Exception as error:
            self.memory.record_rfq_event(
                run_id=run_id,
                event_type="workflow_failed",
                reason="Deterministic workflow processing stopped before completion.",
                details={"error_type": type(error).__name__},
            )
            raise

        return RfqWorkflowResult(
            run=run,
            comparison=comparison,
            anomalies=anomalies,
            audit_events=self.memory.audit_events_for(run_id),
        )

    def _load_memory(
        self, quotes: tuple[NormalizedQuote, ...], observed_on: date
    ) -> tuple[
        dict[str, object],
        dict[str, Decimal],
        dict[str, SupplierQuoteBaseline | None],
    ]:
        """Retrieve performance/quote histories before the current bid is stored."""

        snapshots: dict[str, object] = {}
        performance_scores: dict[str, Decimal] = {}
        quote_baselines: dict[str, SupplierQuoteBaseline | None] = {}
        for quote in quotes:
            performance = self.memory.baseline_for(quote.vendor_id, quote.currency)
            quote_baseline = self.memory.quote_baseline_for(
                quote.vendor_id,
                quote.currency,
                as_of=observed_on,
                lookback_days=self.anomaly_policy.lookback_days,
            )
            if performance is not None:
                performance_scores[quote.vendor_id] = self._performance_score(performance)
            if performance is not None or quote_baseline is not None:
                snapshots[quote.vendor_id] = {
                    "performance_baseline": (
                        performance.model_dump(mode="json") if performance is not None else None
                    ),
                    "quote_baseline": (
                        quote_baseline.model_dump(mode="json")
                        if quote_baseline is not None
                        else None
                    ),
                }
            quote_baselines[quote.quote_id] = quote_baseline
        return snapshots, performance_scores, quote_baselines

    def _detect_anomalies(
        self,
        quotes: tuple[NormalizedQuote, ...],
        quote_baselines: dict[str, SupplierQuoteBaseline | None],
        observed_on: date,
    ) -> tuple[QuoteAnomaly, ...]:
        findings: list[QuoteAnomaly] = []
        for quote in quotes:
            baseline = quote_baselines[quote.quote_id]
            findings.extend(
                detect_quote_anomalies(
                    quote,
                    baseline,
                    self.anomaly_policy,
                    observed_on,
                )
            )
        return tuple(findings)

    @staticmethod
    def _performance_score(baseline: SupplierBaseline) -> Decimal:
        """Derive a bounded score from outcome history, not the current model."""

        responsiveness = baseline.average_responsiveness_score
        delivery_delay = baseline.average_delivery_delay_days
        discount_margin = baseline.average_discount_margin
        delivery_reliability = max(Decimal("0"), Decimal("1") - delivery_delay / Decimal("30"))
        return (
            responsiveness * Decimal("0.50")
            + delivery_reliability * Decimal("0.30")
            + discount_margin * Decimal("0.20")
        )
