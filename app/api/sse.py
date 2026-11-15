"""Safe server-sent-event serialization for RFQ operational facts.

This module deliberately emits persisted audit facts, derived landed-cost results,
and already-reviewed counter-offer drafts. It never streams model prompts, private
supplier document bodies, or chain-of-thought reasoning.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from app.domain.models import QuoteComparison
from app.negotiation.models import CounterOfferDraft
from app.workflow.models import RfqAuditEvent

SSE_COMPLETE_EVENT = "rfq.complete"
SSE_COUNTER_OFFER_EVENT = "rfq.counter_offer"
SSE_LANDED_COST_EVENT = "rfq.landed_cost"
SSE_WORKFLOW_EVENT = "rfq.audit"


def sse_message(event: str, data: dict[str, Any], *, event_id: int | None = None) -> str:
    """Render one RFC-compatible SSE message with JSON data."""

    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, separators=(',', ':'), sort_keys=True, default=str)}")
    return "\n".join(lines) + "\n\n"


def public_audit_event(event: RfqAuditEvent) -> dict[str, Any]:
    """Project an audit event to its approved UI-safe fields."""

    return {
        "sequence": event.sequence,
        "event_type": event.event_type,
        "from_state": event.from_state.value if event.from_state else None,
        "to_state": event.to_state.value if event.to_state else None,
        "reason": event.reason,
        "occurred_at": event.occurred_at.isoformat(),
    }


def public_landed_costs(comparison: QuoteComparison) -> dict[str, Any]:
    """Return deterministic, buyer-visible calculations without input artifacts."""

    return {
        "currency": comparison.currency,
        "quotes": [
            {
                "quote_id": score.quote_id,
                "approval_ready": score.approval_ready,
                "landed_cost": score.landed_cost.model_dump(mode="json"),
            }
            for score in comparison.ranked_quotes
        ],
    }


def public_counter_offer(draft: CounterOfferDraft) -> dict[str, Any]:
    """Return a draft-only thread, never a delivery instruction or transport result."""

    return draft.model_dump(mode="json")


def iter_rfq_events(
    events: tuple[RfqAuditEvent, ...],
    *,
    comparison: QuoteComparison | None = None,
    counter_offer_drafts: tuple[CounterOfferDraft, ...] = (),
    after_sequence: int = 0,
) -> Iterator[str]:
    """Yield new audit facts and matching persisted business snapshots once.

    The route intentionally exposes a finite, reconnect-safe snapshot stream. A
    dashboard reopens it after a state-changing request; `Last-Event-ID` prevents
    duplicate audit facts while the derived data remains safe to replace in UI
    state. Production fan-out can consume the same durable events from Redis.
    """

    new_events = tuple(event for event in events if event.sequence > after_sequence)
    for event in new_events:
        yield sse_message(SSE_WORKFLOW_EVENT, public_audit_event(event), event_id=event.sequence)

    if comparison is not None and any(
        event.event_type == "quotes_analyzed" for event in new_events
    ):
        yield sse_message(SSE_LANDED_COST_EVENT, public_landed_costs(comparison))

    if counter_offer_drafts and any(
        event.event_type == "counter_offer_drafted" for event in new_events
    ):
        for draft in counter_offer_drafts:
            yield sse_message(SSE_COUNTER_OFFER_EVENT, public_counter_offer(draft))

    yield sse_message(
        SSE_COMPLETE_EVENT,
        {"last_sequence": events[-1].sequence if events else 0},
    )
