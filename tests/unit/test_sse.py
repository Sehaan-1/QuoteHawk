"""SSE projection tests: UI receives only safe operational facts."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.api.sse import iter_rfq_events
from app.workflow.models import RfqAuditEvent, RfqRunState


def test_sse_stream_projects_safe_audit_fields_and_supports_resume() -> None:
    event = RfqAuditEvent(
        run_id="run-1",
        sequence=2,
        occurred_at=datetime(2026, 11, 14, tzinfo=UTC),
        event_type="landed_cost_calculated",
        from_state=RfqRunState.NORMALIZED,
        to_state=RfqRunState.ANALYZED,
        reason="Landed costs were calculated for buyer review.",
        details={"hidden": "supplier raw document must never stream"},
    )

    messages = list(iter_rfq_events((event,), after_sequence=1))

    assert messages[0].startswith("id: 2\nevent: rfq.audit\n")
    payload = json.loads(messages[0].split("data: ", 1)[1])
    assert payload == {
        "event_type": "landed_cost_calculated",
        "from_state": "normalized",
        "occurred_at": "2026-11-14T00:00:00+00:00",
        "reason": "Landed costs were calculated for buyer review.",
        "sequence": 2,
        "to_state": "analyzed",
    }
    assert "hidden" not in messages[0]
    assert messages[1] == 'event: rfq.complete\ndata: {"last_sequence":2}\n\n'


def test_sse_stream_skips_already_received_events() -> None:
    event = RfqAuditEvent(
        run_id="run-1",
        sequence=1,
        occurred_at=datetime(2026, 11, 14, tzinfo=UTC),
        event_type="intake_accepted",
        reason="Accepted request.",
    )

    assert list(iter_rfq_events((event,), after_sequence=1)) == [
        'event: rfq.complete\ndata: {"last_sequence":1}\n\n'
    ]
