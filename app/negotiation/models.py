"""Safe counter-offer request and response contracts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DraftProvider(StrEnum):
    """A no-credential fixture is the default; Gemini is explicit and optional."""

    FIXTURE = "fixture"
    GEMINI = "gemini"


class NegotiationRequest(BaseModel):
    """Buyer/policy supplied target, never raw competitor information."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")

    vendor_id: str = Field(min_length=1, max_length=80)
    target_landed_cost: Decimal = Field(gt=0)
    provider: DraftProvider = DraftProvider.FIXTURE


class CounterOfferDraft(BaseModel):
    """A reviewed email draft. It cannot be dispatched by QuoteHawk."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    vendor_id: str
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=4000)
    target_landed_cost: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    provider: DraftProvider
    created_at: datetime
    status: Literal["draft_only"] = "draft_only"
    disclosure_check: str = "passed"
