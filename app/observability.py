"""Local-only structured observability for safe RFQ API operations.

This module intentionally records correlation and operational facts only. It does
not accept request bodies, supplier documents, model traces, commercial values,
credentials, buyer identities, or exception text as log attributes.
"""

from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from enum import Enum
from typing import Final
from uuid import uuid4

REQUEST_ID_HEADER: Final = "X-Request-ID"
_REQUEST_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ALLOWED_ATTRIBUTES: Final = frozenset(
    {
        "method",
        "route",
        "status_code",
        "duration_ms",
        "outcome",
        "run_id",
        "state",
        "quote_count",
        "approval_ready_quote_count",
        "eligible_quote_count",
        "provider",
        "decision",
        "draft_status",
        "execution_boundary",
    }
)
_request_id: ContextVar[str | None] = ContextVar("quotehawk_request_id", default=None)
_logger = logging.getLogger("quotehawk.local_observability")
_configured = False


class _JsonEventFormatter(logging.Formatter):
    """Write one JSON object per line without serializing arbitrary log records."""

    def format(self, record: logging.LogRecord) -> str:
        payload = getattr(record, "quotehawk_payload", None)
        if isinstance(payload, dict):
            return json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return super().format(record)


def configure_local_observability() -> None:
    """Configure an isolated stderr logger once for local operational events.

    The application deliberately does not configure cloud exporters here. A host
    may collect stderr, but QuoteHawk itself only emits the allowlisted fields in
    :func:`record_event`.
    """

    global _configured

    _logger.setLevel(logging.INFO)
    _logger.propagate = False
    if _configured:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonEventFormatter())
    _logger.addHandler(handler)
    _configured = True


def new_or_valid_request_id(candidate: str | None) -> str:
    """Accept a safe client correlation ID or generate a new opaque identifier."""

    if candidate is not None and _REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return str(uuid4())


def bind_request_id(request_id: str) -> Token[str | None]:
    """Bind a request ID for the current async context until reset by middleware."""

    return _request_id.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    """Remove request-local context after a response completes."""

    _request_id.reset(token)


def current_request_id() -> str | None:
    """Return the request correlation ID, if code is executing in an API request."""

    return _request_id.get()


def record_event(event: str, /, **attributes: object) -> None:
    """Emit a concise JSON audit or metric event using only safe operational fields.

    Callers must use a fixed event name and one of ``_ALLOWED_ATTRIBUTES``. Values
    outside the small scalar set are discarded rather than stringified, preventing
    accidental serialization of a request model, supplier payload, or exception.
    """

    payload: dict[str, bool | int | str] = {
        "event": event,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    request_id = current_request_id()
    if request_id is not None:
        payload["request_id"] = request_id
    for name, value in attributes.items():
        if name not in _ALLOWED_ATTRIBUTES:
            continue
        safe_value = _safe_attribute_value(name, value)
        if safe_value is not None:
            payload[name] = safe_value
    _logger.info("quotehawk_operational_event", extra={"quotehawk_payload": payload})


def _safe_attribute_value(name: str, value: object) -> bool | int | str | None:
    """Allow bounded scalar metrics/labels; reject rich or multiline values."""

    if isinstance(value, bool | int):
        return value
    if isinstance(value, Enum):
        value = value.value
    if not isinstance(value, str) or not (0 < len(value) <= 128):
        return None
    if "\n" in value or "\r" in value:
        return None
    if name == "run_id" and not _REQUEST_ID_PATTERN.fullmatch(value):
        # A route parameter can be arbitrary user input. Log a run reference only
        # when it is an opaque identifier rather than a possible sensitive string.
        return None
    return value
