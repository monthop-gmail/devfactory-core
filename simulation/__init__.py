"""End-to-end flow simulation — issue #7, the closing gate on milestone v0.1.

``flows`` holds one definition of each flow the control plane is asked to
demonstrate. ``e2e_flow`` is the runnable simulation over them.

Nothing here is a second state machine: the flows drive the real ``Job`` engine,
the audit trail is the real ``EventLog``, and the forward path they walk is read
out of ``devfactory_core.states`` rather than retyped.
"""

from .flows import (
    MAIN_LINE,
    JobFactory,
    advance_to,
    cancelled_by_a_person,
    failed_at,
    happy_path,
    job_factory,
    main_line,
    never_approved,
    rejected_then_resubmitted,
    stalled_awaiting_approval,
)

__all__ = [
    "MAIN_LINE",
    "JobFactory",
    "advance_to",
    "cancelled_by_a_person",
    "failed_at",
    "happy_path",
    "job_factory",
    "main_line",
    "never_approved",
    "rejected_then_resubmitted",
    "stalled_awaiting_approval",
]
