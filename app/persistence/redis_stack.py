"""Redis supplier-memory cache and durable-work queue adapter.

Redis accelerates tenant-scoped supplier baselines and carries concise RFQ work
signals. The wrapped SQLite/Postgres repository remains the audit source of truth:
raw source artifacts and commercial request bodies are never queued. Selecting a
Redis URL is explicit; a connection failure is surfaced instead of silently
falling back to an in-process cache.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import date
from importlib import import_module
from typing import Any, Protocol, cast

from app.analysis.models import SupplierQuoteBaseline
from app.approval.models import ApprovalRecord, PurchaseOrderDraft
from app.auth.models import DEFAULT_TENANT_ID
from app.domain.models import NormalizedQuote, SupplierBaseline, SupplierObservation
from app.negotiation.models import CounterOfferDraft
from app.persistence.repository import QuoteHawkRepository, RfqRunCreation
from app.workflow.models import RfqAuditEvent, RfqRun, RfqRunState


class RedisStackClient(Protocol):
    """Small synchronous Redis surface that keeps tests independent of a daemon."""

    def get(self, name: str) -> str | bytes | None: ...

    def set(self, name: str, value: str, ex: int | None = None) -> object: ...

    def incr(self, name: str) -> int: ...

    def lpush(self, name: str, *values: str) -> int: ...

    def brpop(self, keys: str, timeout: int = 0) -> tuple[str | bytes, str | bytes] | None: ...

    def ping(self) -> object: ...


def _text(value: str | bytes | None) -> str | None:
    if value is None:
        return None
    return value.decode("utf-8") if isinstance(value, bytes) else value


def _digest(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


class RedisWorkQueue:
    """Queue only safe run-level signals; workers reload durable facts by run ID."""

    def __init__(self, client: RedisStackClient, *, queue_name: str = "quotehawk:rfq-work") -> None:
        self.client = client
        self.queue_name = queue_name

    def enqueue(self, *, run_id: str, tenant_id: str, event_type: str) -> None:
        payload = json.dumps(
            {"run_id": run_id, "tenant_id": tenant_id, "event_type": event_type},
            separators=(",", ":"),
            sort_keys=True,
        )
        self.client.lpush(self.queue_name, payload)

    def dequeue(self, *, timeout: int = 0) -> dict[str, str] | None:
        item = self.client.brpop(self.queue_name, timeout=timeout)
        if item is None:
            return None
        payload = _text(item[1])
        if payload is None:
            return None
        decoded = json.loads(payload)
        if not isinstance(decoded, dict) or not all(
            isinstance(decoded.get(key), str) for key in ("run_id", "tenant_id", "event_type")
        ):
            raise ValueError("Redis queue item does not satisfy the QuoteHawk work contract")
        return cast(dict[str, str], decoded)


class RedisSupplierMemory:
    """Redis-backed supplier-memory cache plus queue hooks over a durable repository.

    Supplier baselines are immutable for a particular Redis version. Writes bump
    that tenant/vendor/currency version, making earlier cache keys unreachable
    without unsafe wildcard deletion. RFQ snapshots, approval records, and PO
    drafts remain in the wrapped transactional repository.
    """

    def __init__(
        self,
        repository: QuoteHawkRepository,
        client: RedisStackClient,
        *,
        cache_ttl_seconds: int = 300,
        queue_name: str = "quotehawk:rfq-work",
    ) -> None:
        if cache_ttl_seconds <= 0:
            raise ValueError("Redis supplier-memory cache TTL must be positive")
        self.repository = repository
        self.client = client
        self.cache_ttl_seconds = cache_ttl_seconds
        self.queue = RedisWorkQueue(client, queue_name=queue_name)

    @classmethod
    def from_url(
        cls,
        repository: QuoteHawkRepository,
        redis_url: str,
        *,
        cache_ttl_seconds: int = 300,
        queue_name: str = "quotehawk:rfq-work",
    ) -> RedisSupplierMemory:
        """Create a Redis client after the optional package and configured URL are present."""

        try:
            redis_module = import_module("redis")
        except ModuleNotFoundError as error:
            if error.name == "redis":
                raise RuntimeError(
                    "QUOTEHAWK_REDIS_URL requires the optional Redis dependency; "
                    "install it with `uv sync --extra redis`."
                ) from error
            raise

        redis_client = redis_module.Redis
        client = redis_client.from_url(redis_url, decode_responses=True)
        client.ping()
        return cls(
            repository,
            cast(RedisStackClient, client),
            cache_ttl_seconds=cache_ttl_seconds,
            queue_name=queue_name,
        )

    def _version_key(self, tenant_id: str, vendor_id: str, currency: str) -> str:
        return f"quotehawk:supplier-version:{_digest(tenant_id, vendor_id, currency.upper())}"

    def _version(self, tenant_id: str, vendor_id: str, currency: str) -> str:
        return _text(self.client.get(self._version_key(tenant_id, vendor_id, currency))) or "0"

    def _invalidate(self, tenant_id: str, vendor_id: str, currency: str) -> None:
        self.client.incr(self._version_key(tenant_id, vendor_id, currency))

    def _cache_key(
        self,
        kind: str,
        tenant_id: str,
        vendor_id: str,
        currency: str,
        *details: str,
    ) -> str:
        return f"quotehawk:{kind}:{_digest(tenant_id, vendor_id, currency.upper(), self._version(tenant_id, vendor_id, currency), *details)}"

    def close(self) -> None:
        """Close Redis and the wrapped durable repository when the application stops."""

        close_client = getattr(self.client, "close", None)
        if callable(close_client):
            close_client()
        close_repository = getattr(self.repository, "close", None)
        if callable(close_repository):
            close_repository()

    def readiness_check(self) -> None:
        self.repository.readiness_check()
        self.client.ping()

    def record_observation(
        self, observation: SupplierObservation, *, tenant_id: str = DEFAULT_TENANT_ID
    ) -> None:
        self.repository.record_observation(observation, tenant_id=tenant_id)
        self._invalidate(tenant_id, observation.vendor_id, observation.currency)

    def baseline_for(
        self, vendor_id: str, currency: str, *, tenant_id: str = DEFAULT_TENANT_ID
    ) -> SupplierBaseline | None:
        key = self._cache_key("supplier-baseline", tenant_id, vendor_id, currency)
        cached = _text(self.client.get(key))
        if cached is not None:
            return SupplierBaseline.model_validate_json(cached)
        baseline = self.repository.baseline_for(vendor_id, currency, tenant_id=tenant_id)
        if baseline is not None:
            self.client.set(key, baseline.model_dump_json(), ex=self.cache_ttl_seconds)
        return baseline

    def record_quote_history(
        self, quote: NormalizedQuote, observed_on: date, *, tenant_id: str = DEFAULT_TENANT_ID
    ) -> None:
        self.repository.record_quote_history(quote, observed_on, tenant_id=tenant_id)
        self._invalidate(tenant_id, quote.vendor_id, quote.currency)

    def quote_baseline_for(
        self,
        vendor_id: str,
        currency: str,
        *,
        tenant_id: str = DEFAULT_TENANT_ID,
        as_of: date,
        lookback_days: int,
    ) -> SupplierQuoteBaseline | None:
        key = self._cache_key(
            "quote-baseline", tenant_id, vendor_id, currency, as_of.isoformat(), str(lookback_days)
        )
        cached = _text(self.client.get(key))
        if cached is not None:
            return SupplierQuoteBaseline.model_validate_json(cached)
        baseline = self.repository.quote_baseline_for(
            vendor_id,
            currency,
            tenant_id=tenant_id,
            as_of=as_of,
            lookback_days=lookback_days,
        )
        if baseline is not None:
            self.client.set(key, baseline.model_dump_json(), ex=self.cache_ttl_seconds)
        return baseline

    def create_rfq_run(
        self,
        *,
        run_id: str,
        requisition_id: str,
        tenant_id: str = DEFAULT_TENANT_ID,
        payload: Mapping[str, Any],
    ) -> RfqRun:
        run = self.repository.create_rfq_run(
            run_id=run_id,
            requisition_id=requisition_id,
            tenant_id=tenant_id,
            payload=payload,
        )
        self.queue.enqueue(run_id=run.run_id, tenant_id=tenant_id, event_type="rfq_created")
        return run

    def create_or_retrieve_rfq_run(
        self,
        *,
        run_id: str,
        requisition_id: str,
        tenant_id: str = DEFAULT_TENANT_ID,
        payload: Mapping[str, Any],
        idempotency_key: str | None = None,
        request_fingerprint: str | None = None,
    ) -> RfqRunCreation:
        creation = self.repository.create_or_retrieve_rfq_run(
            run_id=run_id,
            requisition_id=requisition_id,
            tenant_id=tenant_id,
            payload=payload,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
        if creation.created:
            self.queue.enqueue(
                run_id=creation.run.run_id,
                tenant_id=tenant_id,
                event_type="rfq_created",
            )
        return creation

    def advance_rfq_run(
        self,
        *,
        run_id: str,
        to_state: RfqRunState,
        event_type: str,
        reason: str,
        tenant_id: str = DEFAULT_TENANT_ID,
        details: Mapping[str, Any] | None = None,
        payload_patch: Mapping[str, Any] | None = None,
    ) -> RfqRun:
        run = self.repository.advance_rfq_run(
            run_id=run_id,
            to_state=to_state,
            event_type=event_type,
            reason=reason,
            tenant_id=tenant_id,
            details=details,
            payload_patch=payload_patch,
        )
        self.queue.enqueue(run_id=run_id, tenant_id=tenant_id, event_type=event_type)
        return run

    def request_buyer_approval(
        self,
        *,
        run_id: str,
        eligible_quote_ids: tuple[str, ...],
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> RfqRun:
        run = self.repository.request_buyer_approval(
            run_id=run_id,
            eligible_quote_ids=eligible_quote_ids,
            tenant_id=tenant_id,
        )
        self.queue.enqueue(
            run_id=run_id, tenant_id=tenant_id, event_type="buyer_approval_requested"
        )
        return run

    def complete_buyer_approval(
        self,
        *,
        approval: ApprovalRecord,
        purchase_order_draft: PurchaseOrderDraft | None,
    ) -> RfqRun:
        run = self.repository.complete_buyer_approval(
            approval=approval,
            purchase_order_draft=purchase_order_draft,
        )
        self.queue.enqueue(
            run_id=approval.run_id,
            tenant_id=approval.actor_tenant_id,
            event_type="buyer_approval_recorded",
        )
        return run

    def approval_for(
        self, run_id: str, *, tenant_id: str = DEFAULT_TENANT_ID
    ) -> ApprovalRecord | None:
        return self.repository.approval_for(run_id, tenant_id=tenant_id)

    def purchase_order_draft_for(
        self, run_id: str, *, tenant_id: str = DEFAULT_TENANT_ID
    ) -> PurchaseOrderDraft | None:
        return self.repository.purchase_order_draft_for(run_id, tenant_id=tenant_id)

    def record_rfq_event(
        self,
        *,
        run_id: str,
        event_type: str,
        reason: str,
        tenant_id: str = DEFAULT_TENANT_ID,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.repository.record_rfq_event(
            run_id=run_id,
            event_type=event_type,
            reason=reason,
            tenant_id=tenant_id,
            details=details,
        )
        self.queue.enqueue(run_id=run_id, tenant_id=tenant_id, event_type=event_type)

    def store_counter_offer_draft(
        self,
        *,
        draft: CounterOfferDraft,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> None:
        self.repository.store_counter_offer_draft(draft=draft, tenant_id=tenant_id)
        self.queue.enqueue(
            run_id=draft.run_id,
            tenant_id=tenant_id,
            event_type="counter_offer_drafted",
        )

    def load_rfq_run(self, run_id: str, *, tenant_id: str = DEFAULT_TENANT_ID) -> RfqRun:
        return self.repository.load_rfq_run(run_id, tenant_id=tenant_id)

    def audit_events_for(
        self, run_id: str, *, tenant_id: str = DEFAULT_TENANT_ID
    ) -> tuple[RfqAuditEvent, ...]:
        return self.repository.audit_events_for(run_id, tenant_id=tenant_id)

    def payload_for(self, run_id: str, *, tenant_id: str = DEFAULT_TENANT_ID) -> dict[str, Any]:
        return self.repository.payload_for(run_id, tenant_id=tenant_id)
