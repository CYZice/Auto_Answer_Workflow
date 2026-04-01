from __future__ import annotations

from dataclasses import dataclass


AUTOMATION_STATUSES = {
    "discovered",
    "selected",
    "grabbed",
    "solving",
    "solve_failed",
    "filled",
    "review_pending",
    "ready_to_submit",
    "submitting",
    "submitted",
    "failed_submit",
    "skipped",
    "paused",
    "stopped",
}


ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "discovered": {"selected", "paused", "stopped"},
    "selected": {"grabbed", "paused", "stopped", "skipped"},
    "grabbed": {"solving", "paused", "stopped"},
    "solving": {"solve_failed", "filled", "paused", "stopped"},
    "solve_failed": {"selected", "solving", "paused", "stopped"},
    "filled": {"review_pending", "paused", "stopped"},
    "review_pending": {"ready_to_submit", "skipped", "paused", "stopped"},
    "ready_to_submit": {"submitting", "paused", "stopped"},
    "submitting": {"submitted", "failed_submit", "paused", "stopped"},
    "submitted": set(),
    "failed_submit": {"ready_to_submit", "paused", "stopped"},
    "skipped": {"selected", "paused", "stopped"},
    "paused": {"selected", "grabbed", "review_pending", "ready_to_submit", "stopped"},
    "stopped": {"selected"},
}


@dataclass
class TransitionResult:
    from_status: str
    to_status: str
    valid: bool


def validate_transition(from_status: str, to_status: str) -> TransitionResult:
    if from_status not in AUTOMATION_STATUSES:
        raise ValueError(f"Unknown source status: {from_status}")
    if to_status not in AUTOMATION_STATUSES:
        raise ValueError(f"Unknown target status: {to_status}")

    if to_status in ALLOWED_TRANSITIONS.get(from_status, set()):
        return TransitionResult(
            from_status=from_status, to_status=to_status, valid=True
        )

    raise ValueError(f"Illegal transition: {from_status} -> {to_status}")
