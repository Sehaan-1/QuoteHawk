"""FastAPI endpoints for RFQ runs, safe drafts, approvals, and SSE audit events."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from time import perf_counter
from typing import Never

from fastapi import APIRouter, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

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
from app.observability import (
    REQUEST_ID_HEADER,
    bind_request_id,
    configure_local_observability,
    new_or_valid_request_id,
    record_event,
    reset_request_id,
)
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
    """Attach local services and safe local observability to a FastAPI app."""

    _configure_safe_api_observability(app)
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


def _configure_safe_api_observability(app: FastAPI) -> None:
    """Install correlation middleware and redact validation responses once per app."""

    if getattr(app.state, "quotehawk_observability_configured", False):
        return
    configure_local_observability()

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        _request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        # FastAPI's default validation body can echo untrusted supplier input.
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": "request does not satisfy the QuoteHawk API contract"},
        )

    @app.middleware("http")
    async def correlate_and_measure(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = new_or_valid_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        token = bind_request_id(request_id)
        started_at = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            record_event(
                "http_request_completed",
                method=request.method,
                route=_route_label(request),
                status_code=500,
                duration_ms=_elapsed_milliseconds(started_at),
                outcome="server_error",
            )
            raise
        else:
            response.headers[REQUEST_ID_HEADER] = request_id
            status_code = response.status_code
            record_event(
                "http_request_completed",
                method=request.method,
                route=_route_label(request),
                status_code=status_code,
                duration_ms=_elapsed_milliseconds(started_at),
                outcome=_request_outcome(status_code),
            )
            return response
        finally:
            reset_request_id(token)

    app.state.quotehawk_observability_configured = True


def _route_label(request: Request) -> str:
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    return route_path if isinstance(route_path, str) else "unmatched"


def _elapsed_milliseconds(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))


def _request_outcome(status_code: int) -> str:
    if status_code < 400:
        return "success"
    if status_code < 500:
        return "client_error"
    return "server_error"


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
    if isinstance(error, (json.JSONDecodeError, ValidationError)):
        # Do not echo invalid supplier content or Pydantic's input fragments.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="supplier quote did not satisfy the normalized quote contract",
        ) from error
    if isinstance(error, (ApprovalError, NegotiationGuardrailError, PolicyViolation, ValueError)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    raise error


@router.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    """Provide a side-effect-free local/container liveness signal."""

    return {"status": "ok", "execution_boundary": "draft_only"}


@router.post("/runs", response_model=RfqWorkflowResult, status_code=status.HTTP_201_CREATED)
def create_rfq_run(submission: RfqRunSubmission, request: Request) -> RfqWorkflowResult:
    """Run deterministic policy and persist concise workflow/audit events."""

    workflow, _, _ = _services(request)
    try:
        result = workflow.run(
            submission.requisition,
            submission.quotes,
            observed_on=submission.observed_on,
            run_id=submission.run_id,
        )
    except Exception as error:
        _raise_http_error(error)
    record_event(
        "rfq_run_completed",
        run_id=result.run.run_id,
        state=result.run.state,
        quote_count=len(submission.quotes),
        approval_ready_quote_count=sum(
            score.approval_ready for score in result.comparison.ranked_quotes
        ),
        execution_boundary="draft_only",
    )
    return result


@router.get("/runs/{run_id}", response_model=RfqRunDetail)
def get_rfq_run(run_id: str, request: Request) -> RfqRunDetail:
    """Return persisted state, human-readable audit events, and any buyer result."""

    memory = _memory(request)
    try:
        detail = RfqRunDetail(
            run=memory.load_rfq_run(run_id),
            audit_events=memory.audit_events_for(run_id),
            approval=memory.approval_for(run_id),
            purchase_order_draft=memory.purchase_order_draft_for(run_id),
            payload=memory.payload_for(run_id),
        )
    except Exception as error:
        _raise_http_error(error)
    record_event("rfq_run_retrieved", run_id=run_id, state=detail.run.state)
    return detail


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
    record_event("rfq_audit_stream_opened", run_id=run_id)

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
        approval_request = approvals.open_request(run_id)
    except Exception as error:
        _raise_http_error(error)
    record_event(
        "buyer_approval_requested",
        run_id=run_id,
        state=approval_request.run.state,
        eligible_quote_count=len(approval_request.eligible_quotes),
        execution_boundary="draft_only",
    )
    return approval_request


@router.post("/runs/{run_id}/approvals", response_model=ApprovalResult)
def resolve_approval(
    run_id: str,
    buyer_approval: BuyerApproval,
    request: Request,
) -> ApprovalResult:
    """Record an authorized decision and create a non-binding PO draft if approved."""

    _, approvals, _ = _services(request)
    try:
        result = approvals.decide(run_id, buyer_approval)
    except Exception as error:
        _raise_http_error(error)
    record_event(
        "buyer_approval_resolved",
        run_id=run_id,
        decision=result.record.decision,
        draft_status=(
            result.purchase_order_draft.status if result.purchase_order_draft else "none"
        ),
        execution_boundary="draft_only",
    )
    return result


@router.post("/runs/{run_id}/counter-offers", response_model=CounterOfferDraft)
def draft_counter_offer(
    run_id: str,
    negotiation_request: NegotiationRequest,
    request: Request,
) -> CounterOfferDraft:
    """Return a confidentiality-checked email draft; nothing is sent externally."""

    _, _, negotiation = _services(request)
    try:
        draft = negotiation.draft(run_id, negotiation_request)
    except Exception as error:
        _raise_http_error(error)
    record_event(
        "counter_offer_drafted",
        run_id=run_id,
        provider=draft.provider,
        draft_status=draft.status,
        execution_boundary="draft_only",
    )
    return draft


@router.post("/extract", response_model=NormalizedQuote)
def extract_quote(extraction: QuoteExtractionRequest, request: Request) -> NormalizedQuote:
    """Validate fixture JSON locally or opt in to Gemini structured extraction."""

    try:
        extractor = (
            FixtureQuoteExtractor()
            if extraction.provider is ExtractionProvider.FIXTURE
            else GeminiQuoteExtractor()
        )
        quote = extractor.extract(extraction.raw_quote)
    except Exception as error:
        _raise_http_error(error)
    record_event("quote_extracted", provider=extraction.provider, outcome="validated")
    return quote
