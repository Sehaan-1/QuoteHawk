"""Redis supplier-memory cache and queue behavior without a Redis daemon."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from app.domain.models import SupplierObservation
from app.negotiation.models import NegotiationRequest
from app.negotiation.service import NegotiationService
from app.persistence.redis_stack import RedisSupplierMemory
from app.persistence.sqlite import SqliteSupplierMemory
from app.workflow.engine import RfqWorkflow
from tests.conftest import make_quote


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.queues: dict[str, list[str]] = defaultdict(list)

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str, ex: int | None = None) -> bool:
        del ex
        self.values[name] = value
        return True

    def incr(self, name: str) -> int:
        value = int(self.values.get(name, "0")) + 1
        self.values[name] = str(value)
        return value

    def lpush(self, name: str, *values: str) -> int:
        self.queues[name][0:0] = values
        return len(self.queues[name])

    def brpop(self, keys: str, timeout: int = 0) -> tuple[str, str] | None:
        del timeout
        if not self.queues[keys]:
            return None
        return keys, self.queues[keys].pop()

    def ping(self) -> bool:
        return True


def test_redis_supplier_memory_caches_baselines_and_invalidates_on_observation(tmp_path) -> None:
    durable = SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3")
    redis = _FakeRedis()
    memory = RedisSupplierMemory(durable, redis)
    observation = SupplierObservation(
        vendor_id="vendor-alpha",
        quote_id="history-1",
        observed_on=date(2026, 8, 1),
        currency="USD",
        average_unit_price=Decimal("10"),
        freight_cost=Decimal("100"),
        delivery_delay_days=1,
        responsiveness_score=Decimal("0.9"),
        discount_margin=Decimal("0.1"),
    )

    memory.record_observation(observation)
    first = memory.baseline_for("vendor-alpha", "USD")
    second = memory.baseline_for("vendor-alpha", "USD")

    assert first == second
    assert first is not None
    assert any(key.startswith("quotehawk:supplier-baseline:") for key in redis.values)
    memory.record_observation(observation.model_copy(update={"quote_id": "history-2"}))
    assert memory.baseline_for("vendor-alpha", "USD") is not None


def test_redis_queue_receives_safe_run_signals_without_quote_payloads(
    requisition, tmp_path
) -> None:
    durable = SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3")
    redis = _FakeRedis()
    memory = RedisSupplierMemory(durable, redis)

    RfqWorkflow(memory).run(
        requisition,
        [make_quote(quote_id="QUEUE-1")],
        observed_on=date(2026, 11, 14),
        run_id="queued-run",
    )

    events = []
    while (event := memory.queue.dequeue()) is not None:
        events.append(event)

    assert events[0] == {
        "event_type": "rfq_created",
        "run_id": "queued-run",
        "tenant_id": "local-demo",
    }
    assert "quotes_analyzed" in {event["event_type"] for event in events}
    assert "10.00" not in str(events)
    assert "Alpha Industrial Supply" not in str(events)


def test_redis_queue_marks_a_persisted_draft_thread_without_queuing_its_body(
    requisition, tmp_path
) -> None:
    durable = SqliteSupplierMemory(tmp_path / "quotehawk.sqlite3")
    redis = _FakeRedis()
    memory = RedisSupplierMemory(durable, redis)
    RfqWorkflow(memory).run(
        requisition,
        [make_quote(quote_id="QUEUE-DRAFT")],
        observed_on=date(2026, 11, 14),
        run_id="queued-draft-run",
    )
    while memory.queue.dequeue() is not None:
        pass

    NegotiationService(memory).draft(
        "queued-draft-run",
        NegotiationRequest(vendor_id="vendor-alpha", target_landed_cost=Decimal("1000")),
    )

    assert memory.queue.dequeue() == {
        "event_type": "counter_offer_drafted",
        "run_id": "queued-draft-run",
        "tenant_id": "local-demo",
    }
    assert "Procurement Team" not in str(redis.queues)
