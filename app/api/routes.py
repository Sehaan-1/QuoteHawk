"""FastAPI endpoints for RFQ runs, safe drafts, approvals, and SSE audit events."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from time import perf_counter
from typing import Never, cast

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from psycopg.errors import UniqueViolation
from psycopg_pool import PoolTimeout
from pydantic import ValidationError
from starlette.middleware.base import RequestResponseEndpoint

from app.api.models import (
    QuoteExtractionRequest,
    RfqRunDetail,
    RfqRunSubmission,
    RfqSourceRunSubmission,
)
from app.api.sse import iter_rfq_events
from app.approval.models import ApprovalRequest, ApprovalResult, BuyerApproval
from app.approval.service import ApprovalError, ApprovalService, BuyerApprovalPolicy
from app.approval.webhook import ApprovalWebhookError
from app.auth.models import AuthenticatedPrincipal, AuthMode, Permission
from app.auth.service import (
    AuthenticationError,
    AuthorizationError,
    RequestAuthenticator,
    authenticator_from_environment,
)
from app.domain.models import NormalizedQuote, QuoteComparison
from app.ingestion.extractors import (
    DocumentExtractionError,
    ExtractionProvider,
    FixtureQuoteExtractor,
    GeminiQuoteExtractor,
    extract_supplier_documents,
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
from app.persistence.factory import repository_from_environment
from app.persistence.repository import (
    ConcurrentRunUpdateError,
    IdempotencyConflictError,
    QuoteHawkRepository,
)
from app.policies.pricing import PolicyViolation, PricingPolicy
from app.workflow.engine import RfqWorkflow
from app.workflow.models import RfqWorkflowResult

router = APIRouter(prefix="/api/rfq", tags=["rfq"])
_IDEMPOTENCY_KEY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}")


class InvalidIdempotencyKeyError(ValueError):
    """The request header does not meet the opaque key contract."""


def _idempotency_context(
    submission: RfqRunSubmission | RfqSourceRunSubmission, request: Request
) -> tuple[str | None, str | None]:
    """Validate an opaque retry key and hash normalized input without logging it.

    Source-document requests hash their raw artifact bytes only in memory. The
    persisted fingerprint is one-way; the document contents are never written to
    an RFQ payload or to local structured logs.
    """

    header_value = request.headers.get("Idempotency-Key")
    if header_value is None:
        return None, None
    idempotency_key = header_value.strip()
    if not _IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key):
        raise InvalidIdempotencyKeyError(
            "Idempotency-Key must be an opaque 8-128 character identifier"
        )
    canonical_request = json.dumps(
        submission.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return idempotency_key, hashlib.sha256(canonical_request.encode()).hexdigest()


def configure_quotehawk_api(
    app: FastAPI,
    *,
    memory: QuoteHawkRepository | None = None,
    buyer_policy: BuyerApprovalPolicy | None = None,
    authenticator: RequestAuthenticator | None = None,
) -> None:
    """Attach local services and safe local observability to a FastAPI app."""

    _configure_safe_api_observability(app)
    configured_memory = memory or repository_from_environment()
    configured_buyer_policy = buyer_policy or BuyerApprovalPolicy.from_environment()
    configured_pricing_policy = PricingPolicy.from_environment()
    app.state.quotehawk_memory = configured_memory
    app.state.quotehawk_workflow = RfqWorkflow(
        configured_memory, pricing_policy=configured_pricing_policy
    )
    app.state.quotehawk_approvals = ApprovalService(
        configured_memory,
        policy=configured_buyer_policy,
    )
    app.state.quotehawk_authenticator = authenticator or authenticator_from_environment(
        configured_buyer_policy.authorized_buyer_ids
    )
    app.state.quotehawk_negotiation = NegotiationService(
        configured_memory,
        max_target_reduction_rate=configured_pricing_policy.negotiation_threshold_rate,
    )


def close_quotehawk_api_persistence(app: FastAPI) -> None:
    """Release optional pooled persistence resources during the real app shutdown."""

    memory = getattr(app.state, "quotehawk_memory", None)
    close = getattr(memory, "close", None)
    if callable(close):
        close()


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
    async def correlate_and_measure(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
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
        cast(RfqWorkflow, request.app.state.quotehawk_workflow),
        cast(ApprovalService, request.app.state.quotehawk_approvals),
        cast(NegotiationService, request.app.state.quotehawk_negotiation),
    )


def _memory(request: Request) -> QuoteHawkRepository:
    _services(request)
    return cast(QuoteHawkRepository, request.app.state.quotehawk_memory)


def _principal(
    request: Request,
    permission: Permission,
    *,
    prototype_buyer_id: str | None = None,
) -> AuthenticatedPrincipal:
    _services(request)
    authenticator = cast(RequestAuthenticator, request.app.state.quotehawk_authenticator)
    principal = authenticator.authenticate(
        request.headers.get("Authorization"),
        prototype_buyer_id=prototype_buyer_id,
    )
    authenticator.require(principal, permission)
    return principal


def _assert_tenant_access(
    memory: QuoteHawkRepository,
    run_id: str,
    principal: AuthenticatedPrincipal,
) -> None:
    try:
        memory.load_rfq_run(run_id, tenant_id=principal.tenant_id)
    except KeyError as error:
        # Preserve the local prototype's useful missing-run response while OIDC
        # callers receive a uniform tenant-denied result that does not reveal a
        # cross-tenant run's existence.
        if principal.auth_mode is AuthMode.PROTOTYPE:
            raise
        raise AuthorizationError("identity is not authorized for this RFQ tenant") from error


def _raise_http_error(error: Exception) -> Never:
    if isinstance(error, (sqlite3.IntegrityError, UniqueViolation)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="RFQ run conflicts with an existing persisted record",
        ) from error
    if isinstance(error, KeyError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    if isinstance(error, AuthenticationError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="valid bearer authentication is required",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error
    if isinstance(error, (AuthorizationError, PermissionError)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    if isinstance(error, PoolTimeout):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Postgres connection pool is temporarily unavailable; retry the request.",
        ) from error
    if isinstance(error, ConcurrentRunUpdateError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="RFQ run is being updated; retry with the same idempotency key",
        ) from error
    if isinstance(error, IdempotencyConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    if isinstance(error, InvalidIdempotencyKeyError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    if isinstance(error, (json.JSONDecodeError, ValidationError, DocumentExtractionError)):
        # Do not echo invalid supplier content or Pydantic's input fragments.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="supplier quote did not satisfy the normalized quote contract",
        ) from error
    if isinstance(error, ApprovalWebhookError):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Buyer decision was recorded as draft-only, but its webhook notification failed.",
        ) from error
    if isinstance(error, (ApprovalError, NegotiationGuardrailError, PolicyViolation, ValueError)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    raise error


@router.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    """Provide a side-effect-free local/container liveness signal."""

    return {"status": "ok", "execution_boundary": "draft_only"}


@router.get("/readyz", include_in_schema=False)
def readyz(request: Request) -> dict[str, str]:
    """Check configured storage readiness without returning tenant or RFQ data."""

    try:
        _memory(request).readiness_check()
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="QuoteHawk persistence is not ready",
        ) from error
    return {"status": "ready", "execution_boundary": "draft_only"}


@router.post("/runs", response_model=RfqWorkflowResult, status_code=status.HTTP_201_CREATED)
def create_rfq_run(
    submission: RfqRunSubmission,
    request: Request,
    response: Response,
) -> RfqWorkflowResult:
    """Run deterministic policy and persist concise workflow/audit events."""

    workflow, _, _ = _services(request)
    try:
        principal = _principal(request, Permission.WRITE)
        idempotency_key, request_fingerprint = _idempotency_context(submission, request)
        result = workflow.run(
            submission.requisition,
            submission.quotes,
            fx_rates=submission.fx_rates,
            observed_on=submission.observed_on,
            run_id=submission.run_id,
            tenant_id=principal.tenant_id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
    except Exception as error:
        _raise_http_error(error)
    if result.idempotent_replay:
        response.status_code = status.HTTP_200_OK
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


@router.post("/source-runs", response_model=RfqWorkflowResult, status_code=status.HTTP_201_CREATED)
def create_rfq_source_run(
    submission: RfqSourceRunSubmission,
    request: Request,
    response: Response,
) -> RfqWorkflowResult:
    """Normalize three-to-fifty source documents, then start the durable RFQ flow.

    This endpoint accepts CSV quotations, unstructured supplier email text, and
    base64 PDF invoices/quotations. Source artifacts are processed in memory and
    discarded after strict normalization; only the validated quote facts enter
    the tenant-scoped durable workflow.
    """

    workflow, _, _ = _services(request)
    try:
        principal = _principal(request, Permission.WRITE)
        quotes = extract_supplier_documents(
            submission.supplier_documents,
            provider=submission.provider,
        )
        idempotency_key, request_fingerprint = _idempotency_context(submission, request)
        result = workflow.run(
            submission.requisition,
            quotes,
            fx_rates=submission.fx_rates,
            observed_on=submission.observed_on,
            run_id=submission.run_id,
            tenant_id=principal.tenant_id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
    except Exception as error:
        _raise_http_error(error)
    if result.idempotent_replay:
        response.status_code = status.HTTP_200_OK
    record_event(
        "rfq_source_run_completed",
        run_id=result.run.run_id,
        state=result.run.state,
        quote_count=len(quotes),
        source_count=len(submission.supplier_documents),
        provider=submission.provider,
        execution_boundary="draft_only",
    )
    return result


@router.post("/runs/{run_id}/resume", response_model=RfqWorkflowResult)
def resume_rfq_run(run_id: str, request: Request) -> RfqWorkflowResult:
    """Resume durable deterministic work after a retry or interrupted worker process."""

    workflow, _, _ = _services(request)
    try:
        principal = _principal(request, Permission.WRITE)
        _assert_tenant_access(_memory(request), run_id, principal)
        result = workflow.resume(run_id, tenant_id=principal.tenant_id)
    except Exception as error:
        _raise_http_error(error)
    record_event(
        "rfq_run_resumed",
        run_id=result.run.run_id,
        state=result.run.state,
        execution_boundary="draft_only",
    )
    return result


@router.get("/runs/{run_id}", response_model=RfqRunDetail)
def get_rfq_run(run_id: str, request: Request) -> RfqRunDetail:
    """Return persisted state, human-readable audit events, and any buyer result."""

    memory = _memory(request)
    try:
        principal = _principal(request, Permission.READ)
        _assert_tenant_access(memory, run_id, principal)
        detail = RfqRunDetail(
            run=memory.load_rfq_run(run_id, tenant_id=principal.tenant_id),
            audit_events=memory.audit_events_for(run_id, tenant_id=principal.tenant_id),
            approval=memory.approval_for(run_id, tenant_id=principal.tenant_id),
            purchase_order_draft=memory.purchase_order_draft_for(
                run_id, tenant_id=principal.tenant_id
            ),
            payload=memory.payload_for(run_id, tenant_id=principal.tenant_id),
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
        principal = _principal(request, Permission.READ)
        _assert_tenant_access(memory, run_id, principal)
        events = memory.audit_events_for(run_id, tenant_id=principal.tenant_id)
        if not events and memory.load_rfq_run(run_id, tenant_id=principal.tenant_id):
            raise KeyError(f"no audit events for RFQ run {run_id}")
        payload = memory.payload_for(run_id, tenant_id=principal.tenant_id)
        comparison_payload = payload.get("comparison")
        comparison = (
            QuoteComparison.model_validate(comparison_payload)
            if isinstance(comparison_payload, dict)
            else None
        )
        stored_drafts = payload.get("counter_offer_drafts", {})
        if not isinstance(stored_drafts, dict):
            raise ValueError("RFQ run contains invalid counter-offer draft storage")
        counter_offer_drafts = tuple(
            CounterOfferDraft.model_validate(item) for item in stored_drafts.values()
        )
    except Exception as error:
        _raise_http_error(error)
    record_event("rfq_audit_stream_opened", run_id=run_id)

    last_event_id = request.headers.get("Last-Event-ID", "0")
    try:
        after_sequence = max(0, int(last_event_id))
    except ValueError:
        after_sequence = 0
    return StreamingResponse(
        iter_rfq_events(
            events,
            comparison=comparison,
            counter_offer_drafts=counter_offer_drafts,
            after_sequence=after_sequence,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/runs/{run_id}/approval-requests", response_model=ApprovalRequest)
def open_approval_request(run_id: str, request: Request) -> ApprovalRequest:
    """Pause a budget-eligible run for an authorized buyer's later decision."""

    _, approvals, _ = _services(request)
    try:
        principal = _principal(request, Permission.APPROVE)
        _assert_tenant_access(_memory(request), run_id, principal)
        approval_request = approvals.open_request(run_id, tenant_id=principal.tenant_id)
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
        principal = _principal(
            request,
            Permission.APPROVE,
            prototype_buyer_id=buyer_approval.buyer_id,
        )
        _assert_tenant_access(_memory(request), run_id, principal)
        result = approvals.decide(run_id, buyer_approval, actor=principal)
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
        principal = _principal(request, Permission.WRITE)
        _assert_tenant_access(_memory(request), run_id, principal)
        draft = negotiation.draft(run_id, negotiation_request, tenant_id=principal.tenant_id)
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
        _principal(request, Permission.WRITE)
        if extraction.provider is ExtractionProvider.FIXTURE:
            quote = FixtureQuoteExtractor().extract(extraction.raw_quote)
        elif extraction.provider is ExtractionProvider.GEMINI:
            quote = GeminiQuoteExtractor().extract(extraction.raw_quote)
        else:
            raise ValueError("rule-based document extraction requires the source-runs endpoint")
    except Exception as error:
        _raise_http_error(error)
    record_event("quote_extracted", provider=extraction.provider, outcome="validated")
    return quote
