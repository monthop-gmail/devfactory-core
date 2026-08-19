"""Errors raised by the audit log.

An audit trail earns its name by refusing bad input at the boundary. Once a
record is written it cannot be corrected — ``event/v1`` guarantees append-only —
so every check here has to happen *before* the write, never after.
"""

from __future__ import annotations


class AuditLogError(Exception):
    """Base class for every refusal from this package."""


class DuplicateEvent(AuditLogError):
    """An event_id already present in the log was appended again.

    Accepting it would put two records with one identity into an immutable log,
    and nothing downstream could tell which one was meant.
    """

    def __init__(self, event_id: str) -> None:
        self.event_id = event_id
        super().__init__(f"event_id {event_id!r} is already in the log")


class MissingTenant(AuditLogError):
    """An event arrived with no resolvable tenant.

    RFC-0008: reject at intake rather than guess. Guessing writes one tenant's
    activity into another tenant's audit trail, which is worse than losing the
    event — the loss is visible and the misfiling is not.
    """

    def __init__(self, detail: str = "") -> None:
        suffix = f" — {detail}" if detail else ""
        super().__init__(
            f"event has no resolvable tenant, rejected at intake{suffix}"
        )


class MissingSubject(AuditLogError):
    """An event that cannot say what it is about.

    RFC-0008 made ``job_id`` optional and a subject required, precisely so that
    dropping the job requirement does not leave events that answer nothing.
    """

    def __init__(self, detail: str = "") -> None:
        suffix = f" — {detail}" if detail else ""
        super().__init__(f"event must name its subject{suffix}")


class FabricatedIdentifier(AuditLogError):
    """Something tried to supply a placeholder in place of a real identifier."""

    def __init__(self, field: str, value: str) -> None:
        self.field = field
        self.value = value
        super().__init__(
            f"{field}={value!r} looks fabricated. RFC-0008: absent means absent — "
            f"do not synthesise an identifier to satisfy a field"
        )


class ExternalSourceRequired(AuditLogError):
    """An inbound external event did not say where it came from."""

    def __init__(self) -> None:
        super().__init__(
            "an external event must name its source system so it stays "
            "identifiable as external forever"
        )


# ---- replay ----------------------------------------------------------------
# RFC-0003 asks the log to "enable replayable job history". These are what a
# replay refuses. Each one is a *finding about the log*, not about the caller:
# reaching any of them means the trail cannot account for how the job got where
# it is, which is the failure mode an audit trail exists to make impossible.


class ReplayError(AuditLogError):
    """Base class for a trail that cannot be replayed."""


class EmptyTrail(ReplayError):
    """There is nothing to replay."""

    def __init__(self, job_id: str | None = None) -> None:
        self.job_id = job_id
        subject = f" for job {job_id}" if job_id else ""
        super().__init__(f"no events{subject} — a job with no trail cannot be reconstructed")


class UnstartedTrail(ReplayError):
    """The trail does not begin at ``JOB_CREATED``.

    Every job starts in DRAFT and says so in its first event. A trail starting
    anywhere else is missing its beginning, and replaying it would silently
    assume the beginning it cannot see.
    """

    def __init__(self, job_id: str | None, first_event_type: str) -> None:
        self.job_id = job_id
        self.first_event_type = first_event_type
        super().__init__(
            f"trail for job {job_id} starts at {first_event_type}, not JOB_CREATED — "
            f"its beginning is missing and replay will not assume one"
        )


class BrokenTrail(ReplayError):
    """The trail contradicts itself: a record is missing, or they are out of order.

    A ``STATE_TRANSITION`` names the state it left. If that is not the state the
    replay is standing in, then either a transition between the two was never
    written or the records arrived in the wrong order. Both mean the same thing
    for an audit trail — it can no longer account for the job — so replay stops
    rather than papering over the gap.
    """

    def __init__(self, job_id: str, expected: str, recorded: str, event_id: str) -> None:
        self.job_id = job_id
        self.expected = expected
        self.recorded = recorded
        self.event_id = event_id
        super().__init__(
            f"job {job_id}: event {event_id} records a transition out of {recorded}, "
            f"but the trail so far leaves the job in {expected} — a record is missing "
            f"or the trail is out of order"
        )


class IncompleteSettlement(ReplayError):
    """``COMPLETED`` and ``JOB_COMPLETED`` do not agree in the trail.

    Reaching ``COMPLETED`` emits ``JOB_COMPLETED``, so a trail carrying one and
    not the other is missing a record.

    This is also the only way a trail truncated at the *end* is noticeable.
    Every other record is checked by the one after it naming the state it left;
    nothing follows the last one. ``JOB_COMPLETED`` closes that gap for a job
    that finished — and only for that job. A trail cut short mid-flight, or at
    ``FAILED``, ``CANCELLED``, or ``TIMED_OUT``, still replays cleanly into the
    state it was cut at, because nothing in ``event/v1`` says how many records a
    job should have. Making that detectable needs a per-job sequence number,
    which is a contract change, not something replay can infer.
    """

    def __init__(self, job_id: str, state: str, *, announced: bool) -> None:
        self.job_id = job_id
        self.state = state
        self.announced = announced
        detail = (
            f"the trail carries JOB_COMPLETED but its transitions leave the job in "
            f"{state} — the transition into COMPLETED was never written"
            if announced
            else f"the job reached {state} with no JOB_COMPLETED to announce it"
        )
        super().__init__(f"job {job_id}: {detail}")


class UndeclaredTransition(ReplayError):
    """The trail records an edge the lifecycle does not declare.

    Checked against ``devfactory_core.states``, which is the only place the
    transition table exists. An edge that is not in it was not made by an engine
    following the lifecycle, whatever the record says.
    """

    def __init__(self, job_id: str, from_state: str, to_state: str, event_id: str) -> None:
        self.job_id = job_id
        self.from_state = from_state
        self.to_state = to_state
        self.event_id = event_id
        super().__init__(
            f"job {job_id}: event {event_id} records {from_state} -> {to_state}, which "
            f"the transition table does not declare — the lifecycle is defined in "
            f"devfactory_core.states and nowhere else"
        )


class UnauditedDecision(ReplayError):
    """A job entered a decision state with no decision behind it in the trail.

    Either nothing was recorded, or what was recorded does not produce this
    transition. RFC-0002: an approval that leaves no record is not auditable, and
    a record whose meaning is not the meaning that was decided is worse than
    none. Both are guarantees about the log, so they have to hold when the log is
    read back and not only when it is written.
    """

    def __init__(
        self, job_id: str, state: str, event_id: str, recorded: str | None = None
    ) -> None:
        self.job_id = job_id
        self.state = state
        self.event_id = event_id
        self.recorded = recorded
        detail = (
            f"the decision before it is {recorded}, which does not send a job there"
            if recorded is not None
            else "no GOVERNANCE_DECISION appears before it"
        )
        super().__init__(
            f"job {job_id}: event {event_id} enters {state} and {detail} — the trail "
            f"cannot say who decided, or what they decided"
        )


class UnauditedExecution(ReplayError):
    """The trail shows execution beginning with no APPROVE behind it.

    The direction lock read back off the log: *execution is forbidden before
    APPROVED*. The engine enforces it as it writes; this is the same claim
    checked against what was actually written, by something that was not there
    when it happened.
    """

    def __init__(self, job_id: str, state: str, event_id: str) -> None:
        self.job_id = job_id
        self.state = state
        self.event_id = event_id
        super().__init__(
            f"job {job_id}: event {event_id} enters {state}, but no APPROVE decision "
            f"appears in the trail before it — execution without a recorded approval"
        )


class ExecutionAfterExpiry(ReplayError):
    """The trail shows execution continuing under an approval that had expired.

    The other half of the direction lock, read back off the log. ``approval/v1``
    says an approval past its ``expires_at`` cannot be used to run work; the
    engine refuses it as it writes, and this is the same claim checked against
    what was actually written — by something that was not there at the time and
    can compare the recorded deadline against the recorded moment.

    Distinct from :class:`UnauditedExecution` on purpose. There the trail cannot
    show an approval at all; here it shows one and shows that it had run out,
    which is a different finding about the log and needs different follow-up.
    """

    def __init__(
        self, job_id: str, state: str, event_id: str, *, expired_at: str, occurred_at: str
    ) -> None:
        self.job_id = job_id
        self.state = state
        self.event_id = event_id
        self.expired_at = expired_at
        self.occurred_at = occurred_at
        super().__init__(
            f"job {job_id}: event {event_id} enters {state} at {occurred_at}, but the "
            f"APPROVE in force expired at {expired_at} — the trail records work "
            f"running on an approval that had already lapsed"
        )
