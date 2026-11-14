"""State and audit contracts for a resumable RFQ run."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.analysis.models import QuoteAnomaly
from app.domain.models import QuoteComparison


class RfqRunState(StrEnum):
    """The only workflow states QuoteHawk can persist."""

    INTAKE = "intake"
    NORMALIZED = "normalized"
    ENRICHED_WITH_MEMORY = "enriched_with_memory"
    ANALYZED = "analyzed"
    NEGOTIATION_OR_READY = "negotiation_or_ready"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED_DRAFT = "approved_draft"
    REJECTED = "rejected"


class RfqRun(BaseModel):
    """Durable RFQ run metadata, safe to expose to a later API."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(min_length=1, max_length=100)
    requisition_id: str = Field(min_length=1, max_length=80)
    tenant_id: str = Field(min_length=1, max_length=120)
    state: RfqRunState
    version: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime


class RfqAuditEvent(BaseModel):
    """A concise, non-chain-of-thought record of a run event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    sequence: int = Field(ge=1)
    occurred_at: datetime
    event_type: str = Field(min_length=1, max_length=80)
    from_state: RfqRunState | None = None
    to_state: RfqRunState | None = None
    reason: str = Field(min_length=1, max_length=500)
    details: dict[str, Any] = Field(default_factory=dict)


class RfqWorkflowResult(BaseModel):
    """The deterministic output after QuoteHawk finishes its PR-2 path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run: RfqRun
    comparison: QuoteComparison
    anomalies: tuple[QuoteAnomaly, ...]
    audit_events: tuple[RfqAuditEvent, ...]
