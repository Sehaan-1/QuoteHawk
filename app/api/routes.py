"""FastAPI endpoints for RFQ runs, safe drafts, approvals, and SSE audit events."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from typing import Never

from fastapi import APIRouter, FastAPI, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.api.models import QuoteExtractionRequest, RfqRunDetail, RfqRunSubmission
from app.approval.models import ApprovalRequest, ApprovalResult, BuyerApproval
from app.approval.service import ApprovalError, ApprovalService, BuyerApprovalPolicy
from app.domain.models import NormalizedQuote
from app.ingestion.extractors import (
    ExtractionProvider,
    FixtureQuoteExtractor,
    GeminiQuoteExtractor,
)
from app.negotiation.models import CounterOfferDraft, NegotiationRequest
from app.negotiation.service import NegotiationGuardrailError, NegotiationService
from app.persistence.sqlite import SqliteSupplierMemory
from app.policies.pricing import PolicyViolation
from app.workflow.engine import RfqWorkflow
from app.workflow.models import RfqWorkflowResult

router = APIRouter(prefix="/api/rfq", tags=["rfq"])


def configure_quotehawk_api(
    app: FastAPI,
    *,
    memory: SqliteSupplierMemory | None = None,
    buyer_policy: BuyerApprovalPolicy | None = None,
) -> None:
    """Attach local services; production auth/DB injection is a later concern."""

    configured_memory = memory or SqliteSupplierMemory(
        os.getenv("QUOTEHAWK_DATABASE_PATH", "quotehawk.db")
    )
    app.state.quotehawk_memory = configured_memory
    app.state.quotehawk_workflow = RfqWorkflow(configured_memory)
    app.state.quotehawk_approvals = ApprovalService(
        configured_memory,
        policy=buyer_policy or BuyerApprovalPolicy.from_environment(),
    )
    app.state.quotehawk_negotiation = NegotiationService(configured_memory)


def _services(request: Request) -> tuple[RfqWorkflow, ApprovalService, NegotiationService]:
    if not hasattr(request.app.state, "quotehawk_workflow"):
        configure_quotehawk_api(request.app)
    return (
        request.app.state.quotehawk_workflow,
        request.app.state.quotehawk_approvals,
        request.app.state.quotehawk_negotiation,
    )


def _memory(request: Request) -> SqliteSupplierMemory:
    _services(request)
    return request.app.state.quotehawk_memory


def _raise_http_error(error: Exception) -> Never:
    if isinstance(error, sqlite3.IntegrityError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="RFQ run conflicts with an existing persisted record",
        ) from error
    if isinstance(error, KeyError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    if isinstance(error, PermissionError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    if isinstance(error, (ApprovalError, NegotiationGuardrailError, PolicyViolation, ValueError)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    raise error


@router.post("/runs", response_model=RfqWorkflowResult, status_code=status.HTTP_201_CREATED)
def create_rfq_run(submission: RfqRunSubmission, request: Request) -> RfqWorkflowResult:
    """Run deterministic policy and persist concise workflow/audit events."""

    workflow, _, _ = _services(request)
    try:
        return workflow.run(
            submission.requisition,
            submission.quotes,
            observed_on=submission.observed_on,
            run_id=submission.run_id,
        )
    except Exception as error:
        _raise_http_error(error)


@router.get("/runs/{run_id}", response_model=RfqRunDetail)
def get_rfq_run(run_id: str, request: Request) -> RfqRunDetail:
    """Return persisted state, human-readable audit events, and any buyer result."""

    memory = _memory(request)
    try:
        return RfqRunDetail(
            run=memory.load_rfq_run(run_id),
            audit_events=memory.audit_events_for(run_id),
            approval=memory.approval_for(run_id),
            purchase_order_draft=memory.purchase_order_draft_for(run_id),
            payload=memory.payload_for(run_id),
        )
    except Exception as error:
        _raise_http_error(error)


@router.get("/runs/{run_id}/events")
def stream_rfq_events(run_id: str, request: Request) -> StreamingResponse:
    """Stream persisted business/audit events as SSE; no model reasoning is exposed."""

    memory = _memory(request)
    try:
        events = memory.audit_events_for(run_id)
        if not events and memory.load_rfq_run(run_id):
            raise KeyError(f"no audit events for RFQ run {run_id}")
    except Exception as error:
        _raise_http_error(error)

    def event_stream() -> Iterator[str]:
        for event in events:
            yield f"event: rfq.audit\ndata: {event.model_dump_json()}\n\n"
        yield f"event: rfq.complete\ndata: {json.dumps({'run_id': run_id})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/runs/{run_id}/approval-requests", response_model=ApprovalRequest)
def open_approval_request(run_id: str, request: Request) -> ApprovalRequest:
    """Pause a budget-eligible run for an authorized buyer's later decision."""

    _, approvals, _ = _services(request)
    try:
        return approvals.open_request(run_id)
    except Exception as error:
        _raise_http_error(error)


@router.post("/runs/{run_id}/approvals", response_model=ApprovalResult)
def resolve_approval(
    run_id: str,
    buyer_approval: BuyerApproval,
    request: Request,
) -> ApprovalResult:
    """Record an authorized decision and create a non-binding PO draft if approved."""

    _, approvals, _ = _services(request)
    try:
        return approvals.decide(run_id, buyer_approval)
    except Exception as error:
        _raise_http_error(error)


@router.post("/runs/{run_id}/counter-offers", response_model=CounterOfferDraft)
def draft_counter_offer(
    run_id: str,
    negotiation_request: NegotiationRequest,
    request: Request,
) -> CounterOfferDraft:
    """Return a confidentiality-checked email draft; nothing is sent externally."""

    _, _, negotiation = _services(request)
    try:
        return negotiation.draft(run_id, negotiation_request)
    except Exception as error:
        _raise_http_error(error)


@router.post("/extract", response_model=NormalizedQuote)
def extract_quote(extraction: QuoteExtractionRequest) -> NormalizedQuote:
    """Validate fixture JSON locally or opt in to Gemini structured extraction."""

    try:
        extractor = (
            FixtureQuoteExtractor()
            if extraction.provider is ExtractionProvider.FIXTURE
            else GeminiQuoteExtractor()
        )
        return extractor.extract(extraction.raw_quote)
    except Exception as error:
        _raise_http_error(error)
