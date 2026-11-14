"""Deterministic RFQ workflow with durable state, retries, snapshots, and audits."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.analysis.anomaly_detector import AnomalyPolicy, detect_quote_anomalies
from app.analysis.models import QuoteAnomaly, SupplierQuoteBaseline
from app.auth.models import DEFAULT_TENANT_ID
from app.domain.models import (
    NormalizedQuote,
    PurchaseRequisition,
    QuoteComparison,
    SupplierBaseline,
)
from app.persistence.repository import (
    ConcurrentRunUpdateError,
    IdempotencyConflictError,
    QuoteHawkRepository,
)
from app.policies.pricing import PricingPolicy, compare_quotes
from app.workflow.models import RfqRunState, RfqWorkflowResult

_MAX_CONCURRENT_RESUME_ATTEMPTS = 4


class RfqWorkflow:
    """Run a durable policy-first RFQ flow without model or external side effects.

    Each transition is persisted independently. A process may stop after any
    transition; ``resume`` reloads the durable snapshot and continues only the
    remaining deterministic work. The separate ApprovalService is the only path
    allowed to resolve buyer approval and create a non-binding PO draft.
    """

    def __init__(
        self,
        memory: QuoteHawkRepository,
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
        tenant_id: str = DEFAULT_TENANT_ID,
        idempotency_key: str | None = None,
        request_fingerprint: str | None = None,
    ) -> RfqWorkflowResult:
        """Create a run once, or safely replay/resume the same idempotent request."""

        if (idempotency_key is None) != (request_fingerprint is None):
            raise ValueError("idempotency key and request fingerprint must be supplied together")
        received_quotes = tuple(quotes)
        run_id = run_id or f"rfq-{uuid4()}"
        observed_on = observed_on or date.today()
        creation = self.memory.create_or_retrieve_rfq_run(
            run_id=run_id,
            requisition_id=requisition.requisition_id,
            tenant_id=tenant_id,
            payload={
                "requisition": requisition.model_dump(mode="json"),
                "quotes": [quote.model_dump(mode="json") for quote in received_quotes],
                "observed_on": observed_on.isoformat(),
            },
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
        if not creation.created and creation.request_fingerprint != request_fingerprint:
            raise IdempotencyConflictError(
                "idempotency key was already used for a different RFQ submission"
            )
        return self.resume(
            creation.run.run_id,
            tenant_id=tenant_id,
            idempotent_replay=not creation.created,
        )

    def resume(
        self,
        run_id: str,
        *,
        tenant_id: str = DEFAULT_TENANT_ID,
        idempotent_replay: bool = False,
    ) -> RfqWorkflowResult:
        """Continue a persisted RFQ after a retry or interrupted worker process."""

        for _ in range(_MAX_CONCURRENT_RESUME_ATTEMPTS):
            try:
                return self._resume_once(
                    run_id,
                    tenant_id=tenant_id,
                    idempotent_replay=idempotent_replay,
                )
            except ConcurrentRunUpdateError:
                # A peer completed a compare-and-swap transition. Reloading the
                # durable state is safe because quote-history writes are upserts.
                continue
        raise ConcurrentRunUpdateError("RFQ run changed concurrently; retry from persisted state")

    def _resume_once(
        self,
        run_id: str,
        *,
        tenant_id: str,
        idempotent_replay: bool,
    ) -> RfqWorkflowResult:
        try:
            while True:
                run = self.memory.load_rfq_run(run_id, tenant_id=tenant_id)
                if run.state is RfqRunState.INTAKE:
                    self.memory.advance_rfq_run(
                        run_id=run_id,
                        to_state=RfqRunState.NORMALIZED,
                        event_type="quotes_normalized",
                        reason="Supplier quotes passed strict normalized-quote validation.",
                        tenant_id=tenant_id,
                        details={"quote_count": len(self._quotes_for(run_id, tenant_id))},
                    )
                    continue

                if run.state is RfqRunState.NORMALIZED:
                    quotes, observed_on = self._quotes_and_date_for(run_id, tenant_id)
                    memory_payload, _, _ = self._load_memory(quotes, observed_on, tenant_id)
                    self.memory.advance_rfq_run(
                        run_id=run_id,
                        to_state=RfqRunState.ENRICHED_WITH_MEMORY,
                        event_type="supplier_memory_loaded",
                        reason="Historical supplier baselines loaded before current bids were stored.",
                        tenant_id=tenant_id,
                        details={"vendors_with_history": len(memory_payload)},
                        payload_patch={"supplier_memory": memory_payload},
                    )
                    continue

                if run.state is RfqRunState.ENRICHED_WITH_MEMORY:
                    requisition, quotes, observed_on = self._workflow_inputs_for(run_id, tenant_id)
                    supplier_performance, quote_baselines = self._memory_from_snapshot(
                        quotes,
                        self.memory.payload_for(run_id, tenant_id=tenant_id),
                    )
                    comparison = compare_quotes(
                        requisition,
                        quotes,
                        self.pricing_policy,
                        supplier_performance=supplier_performance,
                    )
                    anomalies = self._detect_anomalies(quotes, quote_baselines, observed_on)
                    self.memory.advance_rfq_run(
                        run_id=run_id,
                        to_state=RfqRunState.ANALYZED,
                        event_type="quotes_analyzed",
                        reason="Landed costs, weighted comparison, and supplier-history anomalies calculated.",
                        tenant_id=tenant_id,
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
                    continue

                if run.state is RfqRunState.ANALYZED:
                    _, quotes, observed_on = self._workflow_inputs_for(run_id, tenant_id)
                    # Persist only after the prior-window analysis. Repeated
                    # recovery uses tenant/quote upserts, so it cannot duplicate history.
                    for quote in quotes:
                        self.memory.record_quote_history(
                            quote,
                            observed_on,
                            tenant_id=tenant_id,
                        )
                    comparison, _ = self._analysis_for(run_id, tenant_id)
                    approval_ready_count = sum(
                        score.approval_ready for score in comparison.ranked_quotes
                    )
                    reason = (
                        "At least one bid is within budget; an authorized buyer may now open the approval gate."
                        if approval_ready_count
                        else "No bid is within budget; a counter-offer draft may be required."
                    )
                    self.memory.advance_rfq_run(
                        run_id=run_id,
                        to_state=RfqRunState.NEGOTIATION_OR_READY,
                        event_type="negotiation_or_readiness_determined",
                        reason=reason,
                        tenant_id=tenant_id,
                        details={
                            "approval_ready_quote_count": approval_ready_count,
                            "execution_boundary": "draft_only",
                        },
                    )
                    continue

                if run.state in {
                    RfqRunState.NEGOTIATION_OR_READY,
                    RfqRunState.AWAITING_APPROVAL,
                    RfqRunState.APPROVED_DRAFT,
                    RfqRunState.REJECTED,
                }:
                    comparison, anomalies = self._analysis_for(run_id, tenant_id)
                    return RfqWorkflowResult(
                        run=run,
                        comparison=comparison,
                        anomalies=anomalies,
                        audit_events=self.memory.audit_events_for(run_id, tenant_id=tenant_id),
                        idempotent_replay=idempotent_replay,
                    )
                raise ValueError("RFQ run has an unsupported workflow state")
        except ConcurrentRunUpdateError:
            raise
        except Exception as error:
            self.memory.record_rfq_event(
                run_id=run_id,
                event_type="workflow_failed",
                reason="Deterministic workflow processing stopped before completion.",
                tenant_id=tenant_id,
                details={"error_type": type(error).__name__},
            )
            raise

    def _workflow_inputs_for(
        self, run_id: str, tenant_id: str
    ) -> tuple[PurchaseRequisition, tuple[NormalizedQuote, ...], date]:
        payload = self.memory.payload_for(run_id, tenant_id=tenant_id)
        return (
            PurchaseRequisition.model_validate(payload["requisition"]),
            tuple(NormalizedQuote.model_validate(item) for item in payload["quotes"]),
            date.fromisoformat(payload["observed_on"]),
        )

    def _quotes_and_date_for(
        self, run_id: str, tenant_id: str
    ) -> tuple[tuple[NormalizedQuote, ...], date]:
        _, quotes, observed_on = self._workflow_inputs_for(run_id, tenant_id)
        return quotes, observed_on

    def _quotes_for(self, run_id: str, tenant_id: str) -> tuple[NormalizedQuote, ...]:
        quotes, _ = self._quotes_and_date_for(run_id, tenant_id)
        return quotes

    def _analysis_for(
        self, run_id: str, tenant_id: str
    ) -> tuple[QuoteComparison, tuple[QuoteAnomaly, ...]]:
        payload = self.memory.payload_for(run_id, tenant_id=tenant_id)
        if "comparison" not in payload:
            raise ValueError("RFQ run has no completed comparison")
        return (
            # Load durable policy output instead of recalculating after an approval
            # state is reached.
            QuoteComparison.model_validate(payload["comparison"]),
            tuple(QuoteAnomaly.model_validate(item) for item in payload.get("anomalies", [])),
        )

    def _memory_from_snapshot(
        self,
        quotes: tuple[NormalizedQuote, ...],
        payload: dict[str, object],
    ) -> tuple[dict[str, Decimal], dict[str, SupplierQuoteBaseline | None]]:
        """Recover exactly the history snapshot persisted before quote analysis."""

        snapshots = payload.get("supplier_memory")
        if not isinstance(snapshots, dict):
            raise ValueError("RFQ run has no persisted supplier-memory snapshot")
        performance_scores: dict[str, Decimal] = {}
        quote_baselines: dict[str, SupplierQuoteBaseline | None] = {}
        for quote in quotes:
            snapshot = snapshots.get(quote.vendor_id)
            if not isinstance(snapshot, dict):
                quote_baselines[quote.quote_id] = None
                continue
            performance_payload = snapshot.get("performance_baseline")
            if isinstance(performance_payload, dict):
                performance_scores[quote.vendor_id] = self._performance_score(
                    SupplierBaseline.model_validate(performance_payload)
                )
            quote_baseline_payload = snapshot.get("quote_baseline")
            quote_baselines[quote.quote_id] = (
                SupplierQuoteBaseline.model_validate(quote_baseline_payload)
                if isinstance(quote_baseline_payload, dict)
                else None
            )
        return performance_scores, quote_baselines

    def _load_memory(
        self,
        quotes: tuple[NormalizedQuote, ...],
        observed_on: date,
        tenant_id: str,
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
            performance = self.memory.baseline_for(
                quote.vendor_id, quote.currency, tenant_id=tenant_id
            )
            quote_baseline = self.memory.quote_baseline_for(
                quote.vendor_id,
                quote.currency,
                tenant_id=tenant_id,
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
