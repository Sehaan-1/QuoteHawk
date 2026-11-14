"""Small persistence contract so SQLite can later be replaced by Postgres."""

from __future__ import annotations

from typing import Protocol

from app.domain.models import SupplierBaseline, SupplierObservation


class SupplierMemoryRepository(Protocol):
    """Persistence needed by the policy/anomaly layers."""

    def record_observation(self, observation: SupplierObservation) -> None:
        """Upsert one auditable supplier observation."""

    def baseline_for(self, vendor_id: str, currency: str) -> SupplierBaseline | None:
        """Return a deterministic vendor baseline for one currency, if present."""
