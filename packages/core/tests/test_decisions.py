"""Governance decisions — RFC-0002's interface, and what it refuses.

The decision *is* the governance layer: everything else in this package exists to
make sure a job cannot execute without one. So most of what is asserted here is a
refusal.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from conftest import drive
from devfactory_core import (
    Decision,
    DecisionType,
    Job,
    JobState,
    Principal,
    Subject,
)
from devfactory_core.decision import new_decision_id
from devfactory_core.errors import (
    CrossTenantDecision,
    DecisionStateMismatch,
    ExecutionBeforeApproval,
    IncompleteDecision,
    InvalidIdentifier,
    InvalidTransition,
    SelfApproval,
    UnmappedDecision,
    WrongDecisionSubject,
)
from devfactory_core.identity import ID_PATTERN
from devfactory_core.states import (
    DECISION_BY_EDGE,
    DECISION_GATE,
    DECISION_TARGET,
    TRANSITIONS,
    decision_for_edge,
)


@pytest.fixture
def reviewer() -> Principal:
    """Someone other than the job's own principal — the usual case."""
    return Principal("human", "bob", display_name="Bob")


def _fresh(alice, clock, **kw) -> Job:
    base = dict(
        job_id="job-001", tenant_id="acme", workspace_id="ws-core", principal=alice, clock=clock
    )
    base.update(kw)
    return Job(**base)


def _at_the_gate(alice, clock, **kw) -> Job:
    job = _fresh(alice, clock, **kw)
    job.submit_for_governance()
    return job


# ---- the vocabulary, RFC-0002 ----------------------------------------------


def test_all_three_decision_types_are_declared():
    """A closed set declared in full — two of three would narrow the contract."""
    assert {d.value for d in DecisionType} == {"APPROVE", "REJECT", "REQUIRE_CHANGES"}


def test_a_decision_only_moves_a_job_along_an_edge_that_already_exists():
    """A decision may not invent a transition — that would be a lifecycle change."""
    declared = TRANSITIONS[DECISION_GATE]
    assert set(DECISION_TARGET.values()) <= set(declared)
    assert DECISION_BY_EDGE == {
        (DECISION_GATE, t): d for d, t in DECISION_TARGET.items()
    }


def test_every_declared_decision_has_a_destination():
    """RFC-0011 closed the last gap — the vocabulary and the table now agree."""
    assert set(DECISION_TARGET) == set(DecisionType)


def test_require_changes_sends_a_job_back_to_draft(alice, reviewer, clock):
    """RFC-0011. Not a rejection, and not a fourteenth state — back to DRAFT."""
    assert DECISION_TARGET[DecisionType.REQUIRE_CHANGES] is JobState.DRAFT

    job = _at_the_gate(alice, clock)
    record = job.decide(
        DecisionType.REQUIRE_CHANGES, authority=reviewer, reason="add a test plan"
    )
    assert job.state is JobState.DRAFT
    assert record.decision is DecisionType.REQUIRE_CHANGES
    assert job.decisions == (record,)
    assert job.history[-1].decision_id == record.decision_id
    assert (job.history[-1].from_state, job.history[-1].to_state) == (
        JobState.GOVERNANCE_ANALYSIS,
        JobState.DRAFT,
    )


def test_require_changes_works_by_its_string_form_too(alice, reviewer, clock):
    job = _at_the_gate(alice, clock)
    job.decide("REQUIRE_CHANGES", authority=reviewer, reason="needs tests")
    assert job.state is JobState.DRAFT


def test_require_changes_has_a_named_method_like_the_other_two(alice, reviewer, clock):
    job = _at_the_gate(alice, clock)
    record = job.require_changes(authority=reviewer, reason="add a test plan")
    assert record.decision is DecisionType.REQUIRE_CHANGES
    assert job.state is JobState.DRAFT


def test_require_changes_leaves_the_job_alive_and_resubmittable(alice, reviewer, clock):
    """``approval/v1``: "งานยังมีชีวิตและกลับมายื่นใหม่ได้"."""
    job = _at_the_gate(alice, clock)
    job.require_changes(authority=reviewer, reason="add a test plan")
    job.submit_for_governance(reason="test plan added")
    job.approve(authority=reviewer, reason="the changes asked for are in")
    assert job.state is JobState.APPROVED
    assert job.decisions[-1].supersedes_decision_id == job.decisions[0].decision_id


def test_require_changes_clears_the_approval_like_a_reject_does(alice, reviewer, clock):
    """Being told to make changes is not being told to proceed — RFC-0011."""
    job = _at_the_gate(alice, clock)
    job.approve(authority=reviewer, reason="first pass")
    job.transition(JobState.TASK_PLANNING)
    job.fail(reason="the approved plan does not work")

    revised = job.supersede(job_id="job-001b")
    revised.submit_for_governance(reason="revised plan")
    revised.require_changes(authority=reviewer, reason="still needs a test plan")
    assert revised.approval is None
    revised.submit_for_governance(reason="test plan added")
    with pytest.raises(InvalidTransition):
        revised.transition(JobState.TASK_PLANNING)


def test_require_changes_and_reject_end_in_the_same_place_by_different_routes(
    alice, reviewer, clock
):
    """RFC-0011's load-bearing claim: the destination is shared, the trail is not.

    This is the whole reason ``REQUIRE_CHANGES -> DRAFT`` does not collapse into
    ``REJECT``. Read the two histories back and one of them stood in ``REJECTED``.
    """
    sent_back = _at_the_gate(alice, clock)
    sent_back.require_changes(authority=reviewer, reason="add a test plan")

    rejected = _at_the_gate(alice, clock, job_id="job-002")
    rejected.reject(authority=reviewer, reason="out of scope")
    rejected.transition(JobState.DRAFT)

    assert sent_back.state is rejected.state is JobState.DRAFT
    assert [h.to_state for h in sent_back.history] == [
        JobState.GOVERNANCE_ANALYSIS,
        JobState.DRAFT,
    ]
    assert [h.to_state for h in rejected.history] == [
        JobState.GOVERNANCE_ANALYSIS,
        JobState.REJECTED,
        JobState.DRAFT,
    ]
    assert JobState.REJECTED not in [h.to_state for h in sent_back.history]


def test_resubmitting_after_a_rejection_is_not_itself_a_decision(alice, reviewer, clock):
    """``REJECTED -> DRAFT`` shares its destination with a decision and is not one.

    If it were read as one it would need an authority, and a replay would demand a
    ``GOVERNANCE_DECISION`` for a verdict nobody made.
    """
    job = _at_the_gate(alice, clock)
    job.reject(authority=reviewer, reason="out of scope")
    before = len(job.decisions)
    job.transition(JobState.DRAFT)  # no authority, no reason, and that is correct
    assert decision_for_edge(JobState.REJECTED, JobState.DRAFT) is None
    assert len(job.decisions) == before
    assert job.history[-1].decision_id is None


def test_a_decision_outside_the_vocabulary_is_not_invented(alice, reviewer, clock):
    job = _at_the_gate(alice, clock)
    with pytest.raises(ValueError):
        job.decide("AUTO_APPROVE", authority=reviewer, reason="looks fine")


def test_a_decision_type_with_no_destination_is_refused_not_guessed(
    alice, reviewer, clock, monkeypatch
):
    """``UnmappedDecision`` has no caller today, and still has a job.

    A future RFC adding a fourth decision type has to declare its edge and its
    destination together. If it declares only the value, this is what it meets —
    a refusal that says so, rather than a ``KeyError`` from inside the engine.
    """
    from devfactory_core import job as job_module

    job = _at_the_gate(alice, clock)
    monkeypatch.setattr(job_module, "DECISION_TARGET", {})
    before = len(job.events)
    with pytest.raises(UnmappedDecision) as excinfo:
        job.approve(authority=reviewer, reason="fine")
    assert "DECISION_TARGET" in str(excinfo.value)
    assert job.state is JobState.GOVERNANCE_ANALYSIS
    assert job.decisions == ()
    assert len(job.events) == before


# ---- the decision record ---------------------------------------------------


def test_approve_records_the_decision_and_moves_the_job(alice, reviewer, clock):
    job = _at_the_gate(alice, clock)
    record = job.approve(authority=reviewer, reason="scope matches milestone v0.1")

    assert isinstance(record, Decision)
    assert record.decision is DecisionType.APPROVE
    assert record.reason == "scope matches milestone v0.1"
    assert record.authority is reviewer
    assert record.subject == Subject("job", "job-001")
    assert record.tenant_id == "acme"
    assert record.workspace_id == "ws-core"
    assert job.state is JobState.APPROVED
    assert job.approval is record
    assert job.decisions == (record,)


def test_decide_is_the_general_form_of_approve_and_reject(alice, reviewer, clock):
    approved = _at_the_gate(alice, clock)
    approved.decide("APPROVE", authority=reviewer, reason="fine")
    assert approved.state is JobState.APPROVED

    rejected = _at_the_gate(alice, clock, job_id="job-002")
    rejected.decide(DecisionType.REJECT, authority=reviewer, reason="missing risk analysis")
    assert rejected.state is JobState.REJECTED


def test_a_decision_is_immutable(alice, reviewer, clock):
    record = _at_the_gate(alice, clock).approve(authority=reviewer, reason="ok")
    with pytest.raises(Exception):
        record.reason = "something else"  # type: ignore[misc]


def test_the_decision_list_is_a_read_only_copy(alice, reviewer, clock):
    job = _at_the_gate(alice, clock)
    job.approve(authority=reviewer, reason="ok")
    assert isinstance(job.decisions, tuple)
    with pytest.raises(AttributeError):
        job.decisions.append("forged")  # type: ignore[attr-defined]


def test_changing_your_mind_cites_the_decision_it_replaces(alice, reviewer, clock):
    """approval/v1: a decision is never edited — the second one references the first."""
    job = _at_the_gate(alice, clock)
    first = job.reject(authority=reviewer, reason="missing risk analysis")
    job.transition(JobState.DRAFT)
    job.submit_for_governance(reason="risk analysis added")
    second = job.approve(authority=reviewer, reason="addressed")

    assert first.supersedes_decision_id is None
    assert second.supersedes_decision_id == first.decision_id
    assert job.decisions == (first, second)


def test_an_explicit_citation_wins_over_the_inferred_one(alice, reviewer, clock):
    job = _at_the_gate(alice, clock)
    earlier = new_decision_id()
    record = job.decide(
        DecisionType.APPROVE,
        authority=reviewer,
        reason="carrying a decision made elsewhere",
        supersedes_decision_id=earlier,
    )
    assert record.supersedes_decision_id == earlier


def test_rejection_leaves_no_approval_behind(alice, reviewer, clock):
    job = _at_the_gate(alice, clock)
    job.reject(authority=reviewer, reason="out of scope")
    assert job.approval is None
    assert job.decisions[-1].decision is DecisionType.REJECT


def test_the_approval_says_who_authorised_execution(alice, reviewer, clock):
    job = drive(_fresh(alice, clock), JobState.IN_PROGRESS, reviewer)
    assert job.approval is not None
    assert job.approval.authority is reviewer
    assert job.history[-1].decision_id is None  # the move into IN_PROGRESS is not a decision


def test_history_links_the_transition_to_the_decision(alice, reviewer, clock):
    job = _at_the_gate(alice, clock)
    record = job.approve(authority=reviewer, reason="ok")
    assert job.history[-1].to_state is JobState.APPROVED
    assert job.history[-1].decision_id == record.decision_id


# ---- the four required meanings --------------------------------------------


@pytest.mark.parametrize("blank", ["", "   ", "\n"], ids=repr)
def test_a_decision_without_a_reason_is_refused(blank, alice, reviewer, clock):
    job = _at_the_gate(alice, clock)
    with pytest.raises(IncompleteDecision) as excinfo:
        job.approve(authority=reviewer, reason=blank)
    assert excinfo.value.field == "reason"
    assert job.state is JobState.GOVERNANCE_ANALYSIS


def test_a_decision_without_an_authority_is_refused(alice, clock):
    job = _at_the_gate(alice, clock)
    with pytest.raises(IncompleteDecision) as excinfo:
        job.approve(authority=None, reason="signed by nobody")  # type: ignore[arg-type]
    assert excinfo.value.field == "authority"


def test_a_decision_without_a_timestamp_is_refused(alice, reviewer):
    with pytest.raises(IncompleteDecision) as excinfo:
        Decision(
            decision_id=new_decision_id(),
            tenant_id="acme",
            subject=Subject("job", "job-001"),
            decision=DecisionType.APPROVE,
            reason="ok",
            authority=reviewer,
            decided_at="2026-08-19T09:00:00+00:00",  # type: ignore[arg-type]
        )
    assert excinfo.value.field == "decided_at"


def test_a_decision_without_a_subject_is_refused(reviewer, clock):
    with pytest.raises(IncompleteDecision) as excinfo:
        Decision(
            decision_id=new_decision_id(),
            tenant_id="acme",
            subject="job-001",  # type: ignore[arg-type]
            decision=DecisionType.APPROVE,
            reason="ok",
            authority=reviewer,
            decided_at=clock(),
        )
    assert excinfo.value.field == "subject"


def test_the_decision_is_timestamped_from_the_job_clock(alice, reviewer, clock):
    job = _at_the_gate(alice, clock)
    record = job.approve(authority=reviewer, reason="ok")
    assert record.decided_at.tzinfo is not None
    assert record.decided_at <= job.events[-1].occurred_at


# ---- no agent has total authority ------------------------------------------


def test_an_agent_cannot_approve_its_own_job(planner, clock):
    job = _at_the_gate(planner, clock)
    before = len(job.events)
    with pytest.raises(SelfApproval):
        job.approve(authority=planner, reason="my own work looks good to me")
    assert job.state is JobState.GOVERNANCE_ANALYSIS
    assert job.approval is None
    assert len(job.events) == before


def test_another_agent_may_approve(planner, clock):
    job = _at_the_gate(planner, clock)
    job.approve(authority=Principal("agent", "reviewer-bot"), reason="independent review")
    assert job.state is JobState.APPROVED


def test_an_agent_may_still_reject_its_own_job(planner, clock):
    """The invariant is about APPROVE — refusing your own work is not a conflict."""
    job = _at_the_gate(planner, clock)
    job.reject(authority=planner, reason="I got the plan wrong")
    assert job.state is JobState.REJECTED


def test_a_person_approving_their_own_job_is_allowed_today(alice, clock):
    """Scope check, not an endorsement.

    ``approval/v1`` and RFC-0002 both state the invariant about *agents*. Making
    the engine stricter than the contract it conforms to needs an RFC here first,
    so this passes deliberately and is flagged rather than silently tightened.
    """
    job = _at_the_gate(alice, clock)
    job.approve(authority=alice, reason="my own job, approved by me")
    assert job.state is JobState.APPROVED


# ---- a decision belongs to what it decides about ---------------------------


def _decision(clock, reviewer, **kw) -> Decision:
    base = dict(
        decision_id=new_decision_id(),
        tenant_id="acme",
        workspace_id="ws-core",
        subject=Subject("job", "job-001"),
        decision=DecisionType.APPROVE,
        reason="ok",
        authority=reviewer,
        decided_at=clock(),
    )
    base.update(kw)
    return Decision(**base)


def test_a_decision_from_another_tenant_is_rejected_not_coerced(alice, reviewer, clock):
    job = _at_the_gate(alice, clock)
    foreign = _decision(clock, reviewer, tenant_id="globex")
    with pytest.raises(CrossTenantDecision) as excinfo:
        job.transition(JobState.APPROVED, reason="ok", principal=reviewer, decision=foreign)
    assert excinfo.value.field == "tenant_id"
    assert job.state is JobState.GOVERNANCE_ANALYSIS
    assert job.tenant_id == "acme"  # not rewritten to match the decision


def test_a_decision_from_another_workspace_is_refused(alice, reviewer, clock):
    job = _at_the_gate(alice, clock)
    foreign = _decision(clock, reviewer, workspace_id="ws-platform")
    with pytest.raises(CrossTenantDecision) as excinfo:
        job.transition(JobState.APPROVED, reason="ok", principal=reviewer, decision=foreign)
    assert excinfo.value.field == "workspace_id"


def test_a_decision_about_another_job_cannot_authorise_this_one(alice, reviewer, clock):
    job = _at_the_gate(alice, clock)
    elsewhere = _decision(clock, reviewer, subject=Subject("job", "job-999"))
    with pytest.raises(WrongDecisionSubject):
        job.transition(JobState.APPROVED, reason="ok", principal=reviewer, decision=elsewhere)


def test_a_decision_that_does_not_produce_this_transition_is_refused(alice, reviewer, clock):
    job = _at_the_gate(alice, clock)
    rejection = _decision(clock, reviewer, decision=DecisionType.REJECT)
    with pytest.raises(DecisionStateMismatch):
        job.transition(JobState.APPROVED, reason="ok", principal=reviewer, decision=rejection)


def test_a_decision_cannot_ride_along_an_ordinary_transition(alice, reviewer, clock):
    job = _at_the_gate(alice, clock)
    job.approve(authority=reviewer, reason="ok")
    stray = _decision(clock, reviewer)
    with pytest.raises(DecisionStateMismatch):
        job.transition(JobState.TASK_PLANNING, decision=stray)


def test_a_decision_must_be_a_decision(alice, clock):
    job = _at_the_gate(alice, clock)
    with pytest.raises(TypeError):
        job.transition(JobState.APPROVED, reason="ok", principal=alice, decision="APPROVE")


# ---- every APPROVE is auditable --------------------------------------------


def test_a_decision_emits_its_own_event_next_to_the_transition(alice, reviewer, clock):
    job = _at_the_gate(alice, clock)
    before = len(job.events)
    record = job.approve(authority=reviewer, reason="ok")

    added = job.events[before:]
    assert [e.type_value for e in added] == ["GOVERNANCE_DECISION", "STATE_TRANSITION"]
    assert added[0].subject_type == "approval"
    assert added[0].subject_id == record.decision_id
    assert added[0].job_id == "job-001"  # RFC-0008: our events always carry it
    assert added[1].transition == {"from": "GOVERNANCE_ANALYSIS", "to": "APPROVED", "reason": "ok"}


def test_every_approve_in_a_run_has_a_governance_decision_event(alice, reviewer, clock):
    job = drive(_fresh(alice, clock), JobState.COMPLETED, reviewer)
    approvals = [d for d in job.decisions if d.decision is DecisionType.APPROVE]
    emitted = [
        p for p in job.event_payloads() if p["event_type"] == "GOVERNANCE_DECISION"
    ]
    assert approvals
    assert {d.decision_id for d in approvals} <= {p["subject_id"] for p in emitted}


def test_ordinary_transitions_emit_no_decision(alice, reviewer, clock):
    job = drive(_fresh(alice, clock), JobState.IN_PROGRESS, reviewer)
    decisions = [e for e in job.events if e.type_value == "GOVERNANCE_DECISION"]
    assert len(decisions) == 1  # the approval, and nothing else


def test_entering_a_decision_state_directly_still_records_a_decision(alice, reviewer, clock):
    """The guarantee is about the state, not about which method was called."""
    job = _at_the_gate(alice, clock)
    job.transition(JobState.APPROVED, reason="approved out of band", principal=reviewer)

    assert job.approval is not None
    assert job.approval.decision is DecisionType.APPROVE
    assert job.approval.authority is reviewer
    assert job.events[-2].type_value == "GOVERNANCE_DECISION"


def test_execution_needs_the_decision_not_just_the_state(alice, clock):
    """The direction lock reads the record, so a forged state does not unlock it."""
    job = _at_the_gate(alice, clock)
    job._state = JobState.APPROVED  # bypass the engine, simulating a bad edit
    with pytest.raises(ExecutionBeforeApproval):
        job.transition(JobState.TASK_PLANNING)


# ---- the approval/v1 wire shape --------------------------------------------

REQUIRED = {"approval_id", "tenant_id", "subject", "decision", "reason", "authority", "decided_at"}


def test_the_payload_carries_everything_approval_v1_requires(alice, reviewer, clock):
    payload = _at_the_gate(alice, clock).approve(authority=reviewer, reason="ok").as_payload()
    assert REQUIRED <= set(payload), f"missing {REQUIRED - set(payload)}"
    assert payload["decision"] == "APPROVE"
    assert payload["subject"] == {"type": "job", "id": "job-001"}
    assert payload["authority"]["id"] == "bob"
    assert payload["tenant_id"] == "acme"
    assert payload["workspace_id"] == "ws-core"


def test_the_payload_uses_the_platforms_field_names(alice, reviewer, clock):
    """RFC-0005: we own the meaning, agent-platform owns the wire names."""
    record = _at_the_gate(alice, clock).approve(authority=reviewer, reason="ok")
    payload = record.as_payload()
    assert payload["approval_id"] == record.decision_id
    assert "decision_id" not in payload


def test_the_event_carries_the_approval_payload(alice, reviewer, clock):
    job = _at_the_gate(alice, clock)
    record = job.approve(authority=reviewer, reason="ok")
    decision_event = [p for p in job.event_payloads() if p["event_type"] == "GOVERNANCE_DECISION"][0]
    assert decision_event["metadata"]["approval"] == record.as_payload()


def test_unset_keys_are_omitted_rather_than_nulled(alice, reviewer, clock):
    """RFC-0008's rule against inventing a value holds for approvals too."""
    payload = _at_the_gate(alice, clock).approve(authority=reviewer, reason="ok").as_payload()
    assert "supersedes_decision_id" not in payload


def test_the_citation_appears_once_there_is_something_to_cite(alice, reviewer, clock):
    job = _at_the_gate(alice, clock)
    first = job.reject(authority=reviewer, reason="no")
    job.transition(JobState.DRAFT)
    job.submit_for_governance()
    payload = job.approve(authority=reviewer, reason="yes").as_payload()
    assert payload["supersedes_decision_id"] == first.decision_id


def test_decision_ids_are_unique_and_well_formed(alice, reviewer, clock):
    ids = [new_decision_id() for _ in range(100)]
    assert len(set(ids)) == len(ids)
    assert all(ID_PATTERN.match(i) for i in ids)


def test_identifiers_on_a_decision_are_validated(reviewer, clock):
    with pytest.raises(InvalidIdentifier):
        _decision(clock, reviewer, tenant_id="ACME")


# ---- expiry, approval/v1 expires_at ----------------------------------------
# "approval ที่หมดอายุแล้วใช้เดินงานไม่ได้ ต้องขอใหม่" — the contract states the meaning
# on an optional field, so the record has to be able to carry it. Issue #17.


def test_an_approval_can_carry_the_deadline_it_was_granted_with(alice, reviewer, clock):
    deadline = datetime(2026, 8, 20, 9, tzinfo=timezone.utc)
    record = _at_the_gate(alice, clock).approve(
        authority=reviewer, reason="ok", expires_at=deadline
    )
    assert record.expires_at == deadline


def test_the_deadline_reaches_the_wire_under_the_contracts_own_name(
    alice, reviewer, clock
):
    deadline = datetime(2026, 8, 20, 9, tzinfo=timezone.utc)
    payload = (
        _at_the_gate(alice, clock)
        .approve(authority=reviewer, reason="ok", expires_at=deadline)
        .as_payload()
    )
    assert payload["expires_at"] == deadline.isoformat()


def test_an_approval_without_a_deadline_says_nothing_about_one(alice, reviewer, clock):
    """Optional in ``approval/v1`` and optional here — absent is not "expired"."""
    record = _at_the_gate(alice, clock).approve(authority=reviewer, reason="ok")
    assert record.expires_at is None
    assert "expires_at" not in record.as_payload()
    assert record.is_expired(datetime(2999, 1, 1, tzinfo=timezone.utc)) is False


def test_expiry_is_inclusive_at_the_moment_named(alice, reviewer, clock):
    """``expires_at`` is when it stops being usable, not the last moment it is."""
    deadline = datetime(2026, 8, 20, 9, tzinfo=timezone.utc)
    record = _at_the_gate(alice, clock).approve(
        authority=reviewer, reason="ok", expires_at=deadline
    )
    assert record.is_expired(deadline - timedelta(seconds=1)) is False
    assert record.is_expired(deadline) is True
    assert record.is_expired(deadline + timedelta(seconds=1)) is True


def test_a_deadline_without_an_offset_is_refused(alice, reviewer, clock):
    """A naive deadline expires at a different moment for every reader."""
    with pytest.raises(ValueError, match="timezone-aware"):
        _at_the_gate(alice, clock).approve(
            authority=reviewer, reason="ok", expires_at=datetime(2026, 8, 20, 9)
        )


def test_a_deadline_that_is_not_a_time_is_refused(alice, reviewer, clock):
    with pytest.raises(ValueError, match="datetime"):
        _at_the_gate(alice, clock).approve(
            authority=reviewer, reason="ok", expires_at="2026-08-20T09:00:00+00:00"
        )


# ---- the subject -----------------------------------------------------------


def test_a_subject_names_a_kind_the_contract_knows(reviewer, clock):
    with pytest.raises(ValueError):
        Subject("banana", "job-001")  # type: ignore[arg-type]


def test_a_subject_id_is_an_identity_v1_id():
    with pytest.raises(InvalidIdentifier):
        Subject("job", "JOB-001")


def test_a_subject_renders_as_type_and_id():
    assert Subject("execution", "exec-1").as_payload() == {"type": "execution", "id": "exec-1"}


def test_repr_names_the_decision_and_who_made_it(alice, reviewer, clock):
    record = _at_the_gate(alice, clock).approve(authority=reviewer, reason="ok")
    text = repr(record)
    assert "APPROVE" in text and "job:job-001" in text and "bob" in text
