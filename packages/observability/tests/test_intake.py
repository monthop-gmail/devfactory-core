"""Intake of events this repository did not produce — RFC-0008."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from devfactory_core import EventType
from devfactory_core.errors import InvalidIdentifier
from devfactory_observability import (
    EventLog,
    MalformedEventType,
    MalformedSequence,
    ExternalSourceRequired,
    FabricatedIdentifier,
    MissingSubject,
    MissingTenant,
    accept_external,
)
from devfactory_observability.intake import PLACEHOLDERS


def test_an_event_with_no_job_is_accepted(external, clock):
    """The whole point: a sighting is a real event that no job caused."""
    event = accept_external(external(), clock=clock)
    assert event.job_id is None
    assert "job_id" not in event.as_payload()


def test_subject_is_required(external, clock):
    with pytest.raises(MissingSubject):
        accept_external(external(subject_id=None), clock=clock)
    with pytest.raises(MissingSubject):
        accept_external(external(subject_type=None), clock=clock)


def test_event_type_is_required(external, clock):
    with pytest.raises(MissingSubject):
        accept_external(external(event_type=None), clock=clock)


def test_payload_must_be_a_mapping(clock):
    with pytest.raises(MissingSubject):
        accept_external(["not", "a", "mapping"], clock=clock)  # type: ignore[arg-type]


# ---- never fabricate, never guess ------------------------------------------


@pytest.mark.parametrize("placeholder", sorted(PLACEHOLDERS - {""}), ids=repr)
def test_a_placeholder_job_id_is_refused(placeholder, external, clock):
    """RFC-0008: absent means absent. A placeholder is neither absent nor real,
    and an immutable log is the wrong place to resolve that ambiguity."""
    with pytest.raises(FabricatedIdentifier):
        accept_external(external(job_id=placeholder), clock=clock)


def test_a_real_job_id_is_kept(external, clock):
    event = accept_external(external(job_id="job-001"), clock=clock)
    assert event.job_id == "job-001"
    assert event.as_payload()["job_id"] == "job-001"


def test_unresolvable_tenant_is_rejected_not_defaulted(external, clock):
    """Guessing writes one tenant's activity into another's trail — worse than
    losing the event, because the loss is visible and the misfiling is not."""
    payload = external()
    del payload["tenant_id"]
    with pytest.raises(MissingTenant):
        accept_external(payload, clock=clock)


def test_a_resolver_that_answers_none_is_a_rejection(external, clock):
    payload = external()
    del payload["tenant_id"]
    with pytest.raises(MissingTenant):
        accept_external(payload, tenant_resolver=lambda _: None, clock=clock)


def test_a_resolver_is_consulted_only_when_needed(external, clock):
    payload = external()
    del payload["tenant_id"]
    event = accept_external(payload, tenant_resolver=lambda _: "globex", clock=clock)
    assert event.tenant_id == "globex"

    # A payload that already names its tenant is not overridden by the resolver.
    kept = accept_external(external(), tenant_resolver=lambda _: "globex", clock=clock)
    assert kept.tenant_id == "acme"


@pytest.mark.parametrize("placeholder", sorted(PLACEHOLDERS - {""}), ids=repr)
def test_a_placeholder_tenant_is_rejected(placeholder, external, clock):
    with pytest.raises(MissingTenant):
        accept_external(external(tenant_id=placeholder), clock=clock)


def test_malformed_identifiers_are_refused(external, clock):
    with pytest.raises(InvalidIdentifier):
        accept_external(external(tenant_id="ACME"), clock=clock)
    with pytest.raises(InvalidIdentifier):
        accept_external(external(subject_id="Sighting 9"), clock=clock)


# ---- source ----------------------------------------------------------------


def test_source_is_required_and_preserved(external, clock):
    with pytest.raises(ExternalSourceRequired):
        accept_external(external(source=None), clock=clock)
    event = accept_external(external(), clock=clock)
    assert event.source == {"kind": "external", "system": "navi-ims"}


def test_kind_is_forced_not_trusted(external, clock):
    """An inbound event is external by the fact of arriving here, whatever it
    claims about itself."""
    event = accept_external(
        external(source={"kind": "internal", "system": "navi-ims"}), clock=clock
    )
    assert event.source["kind"] == "external"


def test_external_events_stay_identifiable_forever(external, clock, make_job):
    log = EventLog()
    log.extend(make_job(tenant_id="acme").events)
    log.append(accept_external(external(), clock=clock))
    kinds = {p["source"]["kind"] for p in log.payloads("acme")}
    assert kinds == {"internal", "external"}


# ---- unrecognised types ----------------------------------------------------


def test_an_unknown_type_is_kept_not_dropped(external, clock):
    """event/v1 platform_rules: keep it, skip interpreting it, never fail."""
    event = accept_external(external(event_type="GEOFENCE_CROSSED"), clock=clock)
    assert event.is_recognised is False
    assert event.type_value == "GEOFENCE_CROSSED"


def test_a_known_type_is_recognised(external, clock):
    event = accept_external(external(event_type="JOB_COMPLETED", job_id="job-001"), clock=clock)
    assert event.event_type is EventType.JOB_COMPLETED
    assert event.is_recognised is True


# ---- optional fields -------------------------------------------------------


def test_actor_workspace_and_correlation_are_carried(external, clock):
    event = accept_external(
        external(
            workspace_id="ws-field",
            correlation_id="corr-1",
            actor={"type": "service", "id": "navi-ims", "display_name": "Navi IMS"},
        ),
        clock=clock,
    )
    payload = event.as_payload()
    assert payload["workspace_id"] == "ws-field"
    assert payload["correlation_id"] == "corr-1"
    assert payload["actor"] == {
        "type": "service",
        "id": "navi-ims",
        "display_name": "Navi IMS",
    }


def test_an_incomplete_actor_is_dropped_rather_than_half_built(external, clock):
    event = accept_external(external(actor={"id": "navi-ims"}), clock=clock)
    assert event.actor is None


def test_workspace_is_optional_for_tenant_level_events(external, clock):
    """ADR-0007 Consequences: tenant-level events have no workspace."""
    event = accept_external(external(), clock=clock)
    assert event.workspace_id is None
    assert "workspace_id" not in event.as_payload()


def test_metadata_is_copied_not_shared(external, clock):
    original = {"record_type": "sighting"}
    event = accept_external(external(metadata=original), clock=clock)
    original["record_type"] = "tampered"
    assert event.metadata["record_type"] == "sighting"


# ---- timestamps ------------------------------------------------------------


def test_the_senders_timestamp_is_used_when_given(external, clock):
    stamp = "2026-08-01T08:30:00+00:00"
    event = accept_external(external(occurred_at=stamp), clock=clock)
    assert event.occurred_at == datetime(2026, 8, 1, 8, 30, tzinfo=timezone.utc)


def test_our_clock_fills_in_only_when_absent(external, clock):
    event = accept_external(external(), clock=clock)
    assert event.occurred_at.year == 2026


def test_an_unparseable_timestamp_is_refused_not_replaced(external, clock):
    """Substituting our clock would misreport when the thing happened, in a
    record nobody can later correct."""
    with pytest.raises(ValueError):
        accept_external(external(occurred_at="last tuesday"), clock=clock)


def test_a_datetime_passes_through(external, clock):
    when = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
    assert accept_external(external(occurred_at=when), clock=clock).occurred_at == when


def test_a_supplied_event_id_is_kept(external, clock):
    event = accept_external(external(event_id="abc123"), clock=clock)
    assert event.event_id == "abc123"


# ---- unknown is kept · malformed is refused --------------------------------


def test_a_well_formed_unknown_type_is_still_kept(external, clock):
    """The rule that matters: not knowing a value is not a reason to refuse it."""
    for name in ("SIGHTING_RECORDED", "MEDICATION_TAKEN", "GEOFENCE_CROSSED", "ABC"):
        event = accept_external(external(event_type=name), clock=clock)
        assert event.type_value == name
        assert event.is_recognised is False


@pytest.mark.parametrize(
    "bad",
    [
        "sighting recorded!!",  # spaces and punctuation
        "lowercase",
        "Mixed_Case",
        "AB",  # shorter than the contract allows
        "9START",  # must begin with a letter
        "TRAILING-DASH",
        "A" * 65,  # longer than the contract allows
    ],
    ids=repr,
)
def test_a_malformed_type_is_refused_at_intake(bad, external, clock):
    """EventTypeName constrains the shape of the name, not the set of values.

    Unknown is a value we have not met; malformed is not a value at all, and
    RFC-0008 puts that refusal at the boundary rather than in the log.
    """
    with pytest.raises(MalformedEventType):
        accept_external(external(event_type=bad), clock=clock)


def test_the_refusal_names_the_contract_rule(external, clock):
    with pytest.raises(MalformedEventType) as excinfo:
        accept_external(external(event_type="not an event type"), clock=clock)
    assert excinfo.value.value == "not an event type"
    assert "EventTypeName" in str(excinfo.value)


def test_every_type_we_emit_ourselves_is_well_formed():
    """A rule that refused our own vocabulary would be the wrong rule."""
    from devfactory_core import EventType
    from devfactory_observability import EVENT_TYPE_PATTERN

    assert all(EVENT_TYPE_PATTERN.match(t.value) for t in EventType)


def test_a_malformed_type_never_reaches_the_log(external, clock):
    log = EventLog()
    with pytest.raises(MalformedEventType):
        log.append(accept_external(external(event_type="bad type"), clock=clock))
    assert len(log) == 0


# ---- sequence is the producer's statement about its own order ---------------


def test_sequence_is_carried_through_untouched(external, clock):
    """event/v1 v1.3.0 · ADR-0015. Dropping it discards the only thing that can
    order events a producer wrote from one clock."""
    event = accept_external(external(sequence=3), clock=clock)
    assert event.sequence == 3
    assert event.as_payload()["sequence"] == 3


def test_sequence_is_optional(external, clock):
    event = accept_external(external(), clock=clock)
    assert event.sequence is None
    assert "sequence" not in event.as_payload()


@pytest.mark.parametrize("bad", [0, -1, "2", 1.5, True, []], ids=repr)
def test_a_sequence_that_is_not_a_position_is_refused(bad, external, clock):
    """Not a smaller ordering claim — not an ordering claim at all."""
    with pytest.raises(MalformedSequence):
        accept_external(external(sequence=bad), clock=clock)


def test_ties_are_resolvable_once_sequence_survives(external, clock):
    """The reader rule event/v1 states: sort by (occurred_at, sequence).

    A batch shares one timestamp, so without sequence the order is unrecoverable
    — which is the whole reason the field exists.
    """
    stamp = "2026-01-01T00:00:00+00:00"
    batch = [
        accept_external(
            external(event_id=f"e-{n}", subject_id="round-1", occurred_at=stamp, sequence=n),
            clock=clock,
        )
        for n in (3, 1, 2)
    ]
    keys = [(e.occurred_at, e.sequence) for e in batch]
    assert len(set(keys)) == 3, "ties must be resolvable"
    ordered = sorted(batch, key=lambda e: (e.occurred_at, e.sequence))
    assert [e.sequence for e in ordered] == [1, 2, 3]
