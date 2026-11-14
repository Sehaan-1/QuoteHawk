"""Transition allowlist for QuoteHawk's deterministic RFQ state machine."""

from __future__ import annotations

from app.workflow.models import RfqRunState


class InvalidStateTransition(ValueError):
    """Raised when code attempts to skip or reverse a workflow boundary."""


_ALLOWED_TRANSITIONS: dict[RfqRunState, frozenset[RfqRunState]] = {
    RfqRunState.INTAKE: frozenset({RfqRunState.NORMALIZED}),
    RfqRunState.NORMALIZED: frozenset({RfqRunState.ENRICHED_WITH_MEMORY}),
    RfqRunState.ENRICHED_WITH_MEMORY: frozenset({RfqRunState.ANALYZED}),
    RfqRunState.ANALYZED: frozenset({RfqRunState.NEGOTIATION_OR_READY}),
    # PR 3 adds an authenticated buyer-approval transition from this state.
    # Keeping it terminal in PR 2 prevents a repository caller from bypassing
    # the future approval check simply by naming an approval state.
    RfqRunState.NEGOTIATION_OR_READY: frozenset(),
    RfqRunState.AWAITING_APPROVAL: frozenset(),
    RfqRunState.APPROVED_DRAFT: frozenset(),
    RfqRunState.REJECTED: frozenset(),
}


def assert_transition(from_state: RfqRunState, to_state: RfqRunState) -> None:
    """Enforce the finite-state workflow before any persistence is mutated."""

    if to_state not in _ALLOWED_TRANSITIONS[from_state]:
        raise InvalidStateTransition(
            f"cannot transition RFQ run from {from_state.value} to {to_state.value}"
        )
