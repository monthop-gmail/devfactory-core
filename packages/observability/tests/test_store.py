"""The log — append-only, tenant-isolated, immutable."""

from __future__ import annotations

import pytest

from devfactory_core import EventType, JobState
from devfactory_observability import DuplicateEvent, EventLog, MissingSubject, MissingTenant


def test_append_records_in_order(make_job):
    job = make_job()
    job.submit_for_governance()
    log = EventLog()
    log.extend(job.events)
    assert [e.type_value for e in log.read("acme")] == ["JOB_CREATED", "STATE_TRANSITION"]


def test_no_mutation_api_exists():
    """Append-only is expressed by the absence of the methods, not by a flag."""
    log = EventLog()
    for forbidden in ("update", "delete", "remove", "clear", "pop", "truncate", "set"):
        assert not hasattr(log, forbidden), f"EventLog must not expose {forbidden}()"


def test_read_returns_a_copy_not_the_live_partition(make_job):
    job = make_job()
    log = EventLog()
    log.extend(job.events)
    got = log.read("acme")
    assert isinstance(got, tuple)
    with pytest.raises(AttributeError):
        got.append("forged")  # type: ignore[attr-defined]
    assert log.count("acme") == 1


def test_stored_events_are_frozen(make_job):
    job = make_job()
    log = EventLog()
    log.extend(job.events)
    with pytest.raises(Exception):
        log.read("acme")[0].tenant_id = "other"  # type: ignore[misc]


def test_duplicate_event_id_is_refused(make_job):
    job = make_job()
    log = EventLog()
    log.append(job.events[0])
    with pytest.raises(DuplicateEvent):
        log.append(job.events[0])
    assert log.count("acme") == 1


def test_event_without_a_tenant_is_refused(make_job, clock):
    from dataclasses import replace

    job = make_job()
    log = EventLog()
    with pytest.raises(MissingTenant):
        log.append(replace(job.events[0], tenant_id=""))
    assert len(log) == 0


def test_event_without_a_subject_is_refused(make_job):
    from dataclasses import replace

    job = make_job()
    log = EventLog()
    with pytest.raises(MissingSubject):
        log.append(replace(job.events[0], subject_id=""))
    assert len(log) == 0


def test_a_refused_append_writes_nothing(make_job):
    job = make_job()
    log = EventLog()
    log.append(job.events[0])
    before = log.digest("acme")
    with pytest.raises(DuplicateEvent):
        log.append(job.events[0])
    assert log.digest("acme") == before


# ---- tenant isolation, RFC-0006 -------------------------------------------


def test_partitions_are_separate_not_a_filtered_list(make_job):
    """RFC-0006: isolation reaches storage. There is no shared list to filter."""
    acme = make_job(job_id="job-001", tenant_id="acme")
    globex = make_job(job_id="job-002", tenant_id="globex")
    log = EventLog()
    log.extend(acme.events)
    log.extend(globex.events)

    assert log.count("acme") == 1
    assert log.count("globex") == 1
    assert {e.tenant_id for e in log.read("acme")} == {"acme"}
    assert {e.tenant_id for e in log.read("globex")} == {"globex"}


def test_no_method_reads_across_tenants(make_job):
    acme = make_job(tenant_id="acme")
    globex = make_job(job_id="job-002", tenant_id="globex")
    log = EventLog()
    log.extend(acme.events)
    log.extend(globex.events)
    # tenants() returns identifiers only; iteration yields identifiers, not events.
    assert log.tenants() == ("acme", "globex")
    assert list(log) == ["acme", "globex"]
    assert len(log) == 2  # a total, carrying no tenant's content


def test_unknown_tenant_reads_as_empty(make_job):
    """Probing for another tenant's existence must tell the caller nothing."""
    log = EventLog()
    log.extend(make_job(tenant_id="acme").events)
    assert log.read("globex") == ()
    assert log.count("globex") == 0


def test_partition_key_comes_from_the_event(make_job):
    """An event cannot be filed under a tenant other than its own."""
    log = EventLog()
    log.extend(make_job(tenant_id="globex").events)
    assert log.tenants() == ("globex",)
    assert log.read("acme") == ()


# ---- filters ---------------------------------------------------------------


def test_read_filters_by_job_subject_and_type(make_job, alice):
    a = make_job(job_id="job-001")
    b = make_job(job_id="job-002")
    a.submit_for_governance()
    log = EventLog()
    log.extend(a.events)
    log.extend(b.events)

    assert len(log.read("acme", job_id="job-001")) == 2
    assert len(log.read("acme", job_id="job-002")) == 1
    assert len(log.read("acme", subject_id="job-002")) == 1
    assert len(log.read("acme", event_type="JOB_CREATED")) == 2
    assert len(log.read("acme", event_type=EventType.STATE_TRANSITION.value)) == 1


def test_payloads_render_wire_shape(make_job):
    log = EventLog()
    log.extend(make_job().events)
    payload = log.payloads("acme")[0]
    assert payload["event_type"] == "JOB_CREATED"
    assert payload["source"]["kind"] == "internal"


# ---- integrity -------------------------------------------------------------


def test_digest_grows_with_appends_and_is_stable_otherwise(make_job):
    job = make_job()
    job.submit_for_governance()
    log = EventLog()
    log.append(job.events[0])
    first = log.digest("acme")
    assert log.digest("acme") == first, "the digest must not change on its own"
    log.append(job.events[1])
    assert log.digest("acme") != first


def test_digest_is_per_tenant(make_job):
    log = EventLog()
    log.extend(make_job(tenant_id="acme").events)
    log.extend(make_job(job_id="job-002", tenant_id="globex").events)
    assert log.digest("acme") != log.digest("globex")
    assert log.digest("nobody") == log.digest("also-nobody")


def test_digest_detects_a_removed_record(make_job):
    """The point of the chain: a shortened history does not reproduce."""
    job = make_job()
    job.submit_for_governance()
    full = EventLog()
    full.extend(job.events)
    tampered = EventLog()
    tampered.append(job.events[0])
    assert full.digest("acme") != tampered.digest("acme")


# ---- no silent state change ------------------------------------------------


def test_every_transition_reaches_the_log(make_job, alice):
    job = make_job()
    job.submit_for_governance()
    job.approve(authority=alice, reason="ok")
    job.transition(JobState.TASK_PLANNING)
    log = EventLog()
    log.extend(job.events)
    transitions = log.read("acme", event_type="STATE_TRANSITION")
    assert len(transitions) == len(job.history)


def test_extend_is_not_atomic_and_says_so(make_job):
    """A partial append keeps what was valid — losing real history to punish a
    later bad record would be the worse failure."""
    job = make_job()
    job.submit_for_governance()
    log = EventLog()
    with pytest.raises(DuplicateEvent):
        log.extend([job.events[0], job.events[1], job.events[0]])
    assert log.count("acme") == 2


def test_repr_names_the_partitions(make_job):
    log = EventLog()
    assert "empty" in repr(log)
    log.extend(make_job().events)
    assert "acme=1" in repr(log)
