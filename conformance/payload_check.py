#!/usr/bin/env python3
"""Validate the payloads this repository actually produces against the pinned contracts.

This is ADR-0006 requirement 2 for a consumer: a conformance test in CI that
validates **real payloads**, not a check that ``$ref`` strings point somewhere.
The difference matters — ``care-agent-platform`` pinned ``event/v1`` correctly and
had every ``$ref`` right, and its first run still found three payloads that did
not conform. Declared is not conforming.

So this file runs a scenario through the real job state machine and the real
event log, collects what they emit, and validates that. Nothing is hand-written
to please the schema.

It also asserts the ``event/v1`` guarantees that JSON Schema cannot express —
append-only, no silent state change, no fabricated identifiers, no reasoning
traces in an audit record.

The governance decisions those jobs produced are validated the same way against
``approval/v1``, together with the guarantees RFC-0002 owns: every APPROVE leaves
a record, and a ``REQUIRE_CHANGES`` sends the job back to ``DRAFT`` without its
trail ever passing through ``REJECTED`` — RFC-0011, which is what keeps
*"REQUIRE_CHANGES ไม่ใช่ REJECT"* true of the record and not only of the wording.

Their *names* are checked too, because ``approval/v1`` leaves
``additionalProperties`` open and so cannot reject a field we named ourselves.

Usage::

    python3 conformance/payload_check.py            # fetch schemas, then validate
    python3 conformance/payload_check.py --offline   # reuse the cache, no network
    python3 conformance/payload_check.py --json      # machine-readable result

The approach follows ``care-agent-platform/conformance/payload_check.py``, which
got here first and is worth copying rather than reinventing.
"""

from __future__ import annotations

import argparse
import copy
import json
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT),
    str(ROOT / "packages" / "core"),
    str(ROOT / "packages" / "observability"),
]

PINNED = ROOT / "conformance" / "pinned.yaml"
CACHE = ROOT / "conformance" / ".schema_cache"
RAW = "https://raw.githubusercontent.com/{repo}/{commit}/contracts/{path}"

findings: list[tuple[str, str, str]] = []
passed = 0


def ok(area: str, message: str) -> None:
    global passed
    passed += 1
    findings.append(("ok", area, message))
    print(f"  ok    {area}: {message}")


def fail(area: str, message: str) -> None:
    findings.append(("FAIL", area, message))
    print(f"  FAIL  {area}: {message}")


# ---- schemas ---------------------------------------------------------------


def load_pinned() -> dict:
    import yaml

    return yaml.safe_load(PINNED.read_text(encoding="utf-8"))


def fetch_schemas(pinned: dict, *, offline: bool) -> dict[str, dict]:
    """Read each pinned schema, from cache when present, from the pinned commit otherwise.

    The cache key includes the commit, so bumping the pin cannot silently reuse
    the previous contract's files.
    """
    import yaml

    repo, commit = pinned["repo"], pinned["commit"]
    CACHE.mkdir(exist_ok=True)
    schemas: dict[str, dict] = {}
    for path in pinned["schemas"]:
        cached = CACHE / f"{commit[:8]}-{path.replace('/', '_')}"
        if not cached.exists():
            if offline:
                raise SystemExit(
                    f"✗ ไม่มี cache ของ {path} — รันหนึ่งครั้งโดยไม่ใส่ --offline ก่อน"
                )
            with urllib.request.urlopen(
                RAW.format(repo=repo, commit=commit, path=path), timeout=30
            ) as response:
                cached.write_bytes(response.read())
        document = yaml.safe_load(cached.read_text(encoding="utf-8"))
        schemas[document["$id"]] = document
    return schemas


def as_json_schema(document: dict, non_schema_keys: list[str]) -> dict:
    """Strip the keys the platform uses for provenance and semantics.

    ``derived_from``, ``guarantees``, and ``platform_rules`` are how
    ``agent-platform`` records where a contract came from and what may not change
    about it. They are meaningful and they are not JSON Schema, so a validator has
    to be handed the document without them.
    """
    stripped = copy.deepcopy(document)
    for key in non_schema_keys:
        stripped.pop(key, None)
    return stripped


def build_validator(schemas: dict[str, dict], target_id: str, non_schema_keys: list[str]):
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    registry = Registry().with_resources(
        [
            (uri, Resource.from_contents(as_json_schema(doc, non_schema_keys)))
            for uri, doc in schemas.items()
        ]
    )
    return Draft202012Validator(
        as_json_schema(schemas[target_id], non_schema_keys), registry=registry
    )


# ---- the scenario ----------------------------------------------------------


def run_scenario():
    """Drive real jobs through the real engine and return the log plus what happened.

    Eight jobs across two tenants, covering every terminal state, the mid-run
    approval pause, rejection and resubmission, being sent back for changes,
    recovery by supersession, and an approval that expired where it sat — plus two
    inbound external events that no job caused.

    The journeys themselves are ``simulation/flows.py``, which issue #7 made the
    one place they are written down. Two files describing the same lifecycle
    differently is how they end up disagreeing about it, and this check exists to
    catch disagreement rather than to add one.
    """
    from datetime import datetime, timedelta, timezone

    from devfactory_core import JobState, Principal
    from devfactory_observability import EventLog, accept_external
    from simulation.flows import (
        approval_expired,
        cancelled_by_a_person,
        failed_at,
        happy_path,
        job_factory,
        rejected_then_resubmitted,
        require_changes_then_resubmitted,
        stalled_awaiting_approval,
    )

    log = EventLog()
    owner = Principal("human", "alice", display_name="Alice")
    reviewer = Principal("human", "bob", display_name="Bob")
    acme = job_factory(tenant_id="acme", workspace_id="ws-core", principal=owner)
    globex = job_factory(
        tenant_id="globex", workspace_id="ws-platform", principal=owner
    )

    # 1. the happy path, with a mid-run approval pause before deploy
    happy = happy_path(
        acme, job_id="job-001", authority=reviewer, pause_in=JobState.DEPLOYABLE
    )

    # 2. rejected, revised, resubmitted, approved
    revised = rejected_then_resubmitted(acme, job_id="job-002", authority=reviewer)

    # 3. failed, then superseded by a fresh job that re-enters governance
    failed = failed_at(
        acme, job_id="job-003", authority=reviewer, state=JobState.IN_PROGRESS
    )
    replacement = failed.supersede(job_id="job-004", principal=owner)
    replacement.submit_for_governance(reason="retry with a different plan")

    # 4. cancelled by a person
    cancelled = cancelled_by_a_person(acme, job_id="job-005", owner=owner)

    # 5. an approval nobody answered, in a second tenant
    stalled = stalled_awaiting_approval(globex, job_id="job-006", authority=reviewer)

    # 6. an approval that expired where it sat — the only flow that produces an
    #    approval/v1 payload carrying expires_at, so the field is validated as a
    #    real payload rather than asserted about in the abstract
    expired = approval_expired(
        acme,
        job_id="job-008",
        authority=reviewer,
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )

    # 7. sent back for changes, revised, resubmitted, approved — RFC-0011. The only
    #    flow that produces an approval/v1 payload carrying decision=REQUIRE_CHANGES,
    #    so the third value of a closed vocabulary is validated as something the
    #    engine really emitted rather than asserted about in the abstract.
    sent_back = require_changes_then_resubmitted(
        acme, job_id="job-011", authority=reviewer
    )

    jobs = [happy, revised, failed, replacement, cancelled, stalled, expired, sent_back]
    for job in jobs:
        log.extend(job.events)

    # 7. events from another system, which no job caused
    external = [
        accept_external(
            {
                "event_type": "SIGHTING_RECORDED",
                "tenant_id": "acme",
                "subject_type": "external",
                "subject_id": "sighting-4471",
                "source": {"system": "navi-ims"},
                "actor": {"type": "service", "id": "navi-ims"},
                "metadata": {"record_type": "sighting"},
            }
        ),
        accept_external(
            {
                "event_type": "GEOFENCE_CROSSED",
                "tenant_id": "globex",
                "subject_type": "external",
                "subject_id": "device-88",
                "correlation_id": "corr-geo-1",
                "source": {"system": "navi-ims"},
            }
        ),
    ]
    log.extend(external)
    return log, jobs, external


# ---- checks ----------------------------------------------------------------


def is_known_gap(payload: dict, error, gaps: list[dict]) -> dict | None:
    """Match one validation error against the recorded gaps, as narrowly as possible.

    A gap has to name both the field it applies to and the kind of event, so it
    cannot quietly widen into a general excuse. Anything that does not match both
    fails the run.
    """
    path = "/".join(str(part) for part in error.path)
    for gap in gaps or ():
        when = gap.get("tolerated_when") or {}
        if when.get("json_path") != path:
            continue
        if when.get("source_kind") != (payload.get("source") or {}).get("kind"):
            continue
        return gap
    return None


def check_payloads(log, validator, gaps: list[dict]) -> None:
    total = 0
    real = 0
    tolerated: dict[str, int] = {}
    for tenant in log.tenants():
        for payload in log.payloads(tenant):
            total += 1
            for error in sorted(validator.iter_errors(payload), key=lambda e: list(e.path)):
                gap = is_known_gap(payload, error, gaps)
                if gap is not None:
                    tolerated[gap["id"]] = tolerated.get(gap["id"], 0) + 1
                    continue
                real += 1
                where = "/".join(str(part) for part in error.path) or "<root>"
                fail(
                    "payload",
                    f"{payload['event_type']} ({payload['event_id'][:8]}): "
                    f"{error.message} at {where}",
                )
    if real == 0:
        ok("payload", f"{total} event ผ่าน event/v1 (ยกเว้นช่องว่างที่รู้ตัว)")
    for gap_id, count in sorted(tolerated.items()):
        gap = next(g for g in gaps if g["id"] == gap_id)
        print(
            f"  gap   payload: {count} event ติดช่องว่างที่รู้ตัว {gap_id} "
            f"— {gap['issue']} (หมดอายุ {gap['expires']})"
        )


def check_field_names(approvals: list[dict], approval_schema: dict) -> None:
    """Do our approval payloads spell the fields the way ``approval/v1`` spells them?

    JSON Schema will not answer this. ``approval/v1`` does not set
    ``additionalProperties: false``, so a payload carrying an invented key — or a key
    this repository named itself while waiting for the contract to grow one — passes
    validation in silence. That silence is what let ``supersedes_decision_id`` ride the
    wire for a day, and it would let the next one ride longer.

    So the check is the contract's own property list, used as the closed set the
    contract does not declare it to be. Anything outside it is ours, not theirs, and
    has to be either renamed or registered as a gap.
    """
    allowed = set(approval_schema.get("properties") or ())
    strangers: dict[str, int] = {}
    for payload in approvals:
        for key in payload:
            if key not in allowed:
                strangers[key] = strangers.get(key, 0) + 1
    if strangers:
        fail(
            "field-names",
            "field ที่ไม่มีใน approval/v1 (schema ไม่ปิด additionalProperties จึงยัง valid): "
            + ", ".join(f"{k} ×{n}" for k, n in sorted(strangers.items())),
        )
    else:
        ok(
            "field-names",
            f"{len(approvals)} approval ใช้ชื่อ field ของ approval/v1 ทั้งหมด "
            f"— ไม่มี field ที่เราตั้งชื่อเอง",
        )

    # The positive half. The check above is satisfied by a payload that carries no
    # citation at all, and "the link quietly stopped being sent" is the other way this
    # rename could go wrong. approval/v1: "การเปลี่ยนใจคือ approval ใบใหม่ที่อ้างใบเดิม"
    citing = [p for p in approvals if p.get("supersedes_approval_id")]
    retired = [p for p in approvals if "supersedes_decision_id" in p]
    if retired:
        fail(
            "field-names",
            f"{len(retired)} approval ยังส่ง supersedes_decision_id — "
            f"approval/v1 v1.1.0 ตั้งชื่อ field นี้ว่า supersedes_approval_id แล้ว",
        )
    elif citing:
        ok(
            "field-names",
            f"{len(citing)} approval อ้างใบก่อนหน้าผ่าน supersedes_approval_id "
            f"(approval/v1 v1.1.0)",
        )
    else:
        fail(
            "field-names",
            "ไม่มี approval ใบไหนอ้างใบเดิมเลย — scenario มีการเปลี่ยนใจอยู่ "
            "ถ้าไม่มี supersedes_approval_id แปลว่าลิงก์หายไประหว่างทาง",
        )


def check_decisions(log, jobs, validator, approval_schema) -> None:
    """RFC-0002 — the decisions this engine produced, judged by ``approval/v1``.

    Same rule as the events above: nothing is hand-written to please the schema.
    These are the records the real state machine wrote while the scenario ran.
    """
    decisions = [
        payload
        for tenant in log.tenants()
        for payload in log.payloads(tenant)
        if payload["event_type"] == "GOVERNANCE_DECISION"
    ]
    real = 0
    for payload in decisions:
        approval = (payload.get("metadata") or {}).get("approval")
        if approval is None:
            real += 1
            fail(
                "approval",
                f"GOVERNANCE_DECISION {payload['event_id'][:8]} ไม่มี approval payload",
            )
            continue
        for error in sorted(validator.iter_errors(approval), key=lambda e: list(e.path)):
            real += 1
            where = "/".join(str(part) for part in error.path) or "<root>"
            fail(
                "approval",
                f"{approval.get('decision')} ({str(approval.get('approval_id'))[:8]}): "
                f"{error.message} at {where}",
            )
    if real == 0:
        ok("approval", f"{len(decisions)} decision ผ่าน approval/v1")

    # Valid is not the same as correctly named — see check_field_names.
    check_field_names(
        [
            approval
            for payload in decisions
            if (approval := (payload.get("metadata") or {}).get("approval")) is not None
        ],
        approval_schema,
    )

    # "ทุก APPROVE ต้อง auditable — ต้องมี event GOVERNANCE_DECISION คู่กันเสมอ"
    mismatched = [
        job.job_id
        for job in jobs
        if len([h for h in job.history if h.to_state.value == "APPROVED"])
        != len([d for d in job.decisions if d.decision.value == "APPROVE"])
    ]
    if mismatched:
        fail("approval", f"เข้า APPROVED โดยไม่มี decision record: {mismatched}")
    else:
        ok("approval", "ทุก APPROVE มี decision record และ event คู่กับ STATE_TRANSITION")

    # "approval ที่หมดอายุแล้วใช้เดินงานไม่ได้ ต้องขอใหม่" (approval/v1 expires_at)
    # เขาเขียนความหมายไว้ใน schema แล้ว แต่ JSON Schema ตรวจให้ไม่ได้ว่า engine ทำตามไหม
    # จึงต้องตรวจที่นี่ · ดู rfcs/0007 Amendment 1 · issue #17
    expiring = [
        payload
        for payload in decisions
        if (payload.get("metadata") or {}).get("approval", {}).get("expires_at")
    ]
    if expiring:
        ok("approval", f"{len(expiring)} approval พก expires_at และผ่าน approval/v1")
    else:
        fail(
            "approval",
            "ไม่มี approval ที่พก expires_at เลย — เช็คข้างล่างจะกลายเป็นการตรวจสิ่งที่ไม่มีจริง",
        )
    check_expiry_enforced()

    # REQUIRE_CHANGES มีปลายทางแล้วตาม rfcs/0011 — DRAFT ตรงจากประตู
    # เดิมเช็คนี้ยืนยันว่า engine ปฏิเสธค่านี้ ตอนนี้กลับด้านเป็นยืนยันว่ามันทำงานถูก
    # ดู states.DECISION_TARGET
    from devfactory_core import DecisionType, Job, JobState, Principal

    probe = Job(
        job_id="job-007",
        tenant_id="acme",
        workspace_id="ws-core",
        principal=Principal("human", "alice"),
    )
    probe.submit_for_governance()
    probe.decide(
        DecisionType.REQUIRE_CHANGES,
        authority=Principal("human", "bob"),
        reason="needs a test plan",
    )
    if probe.state is JobState.DRAFT and probe.approval is None:
        ok("approval", "REQUIRE_CHANGES พา job กลับไป DRAFT และไม่เหลือ approval ค้างไว้ (rfcs/0011)")
    else:
        fail(
            "approval",
            f"REQUIRE_CHANGES พา job ไปจบที่ {probe.state.value} "
            f"(approval={probe.approval is not None}) — ต้องเป็น DRAFT และไม่มี approval",
        )

    # "REQUIRE_CHANGES ไม่ใช่ REJECT" — ปลายทางเดียวกันกับ REJECT แต่คนละเส้นทาง
    # เส้นทางอยู่ใน trail จริง ไม่ใช่แค่ใน decision record จึงตรวจที่ history
    visited = [h.to_state.value for h in probe.history]
    if visited == ["GOVERNANCE_ANALYSIS", "DRAFT"]:
        ok("approval", "REQUIRE_CHANGES ข้าม REJECTED — trail แยกออกจากการถูกปฏิเสธ")
    else:
        fail("approval", f"เส้นทางของ REQUIRE_CHANGES คือ {visited} — คาดว่าต้องข้าม REJECTED")


def check_expiry_enforced() -> None:
    """Would we notice a job still running on an approval that had expired?

    Two sides, because the guarantee has two. The engine must refuse to move the
    job; and if something else moved it anyway, the trail must not read back as
    though that were fine — the deadline and the moment are both recorded, so the
    log can be judged against itself by a reader who was not there.

    The second half is checked by forging a trail rather than by producing one,
    for the same reason the ``REQUIRE_CHANGES`` probe below exists: the engine
    cannot produce the record we need to catch, and a check that can only see
    records the engine agrees with is not checking the engine.
    """
    import dataclasses
    from datetime import datetime, timedelta, timezone

    from devfactory_core import JobState, Principal
    from devfactory_core.errors import ExpiredApproval
    from devfactory_observability import ExecutionAfterExpiry, replay_job
    from simulation.flows import job_factory

    # A clock that can be pushed forward, because an approval expires by time
    # passing and nothing else. Granting one that was already stale would be a
    # different, less interesting record.
    start = datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc)
    now = {"t": start}

    def clock() -> datetime:
        now["t"] += timedelta(seconds=1)
        return now["t"]

    owner = Principal("human", "alice")
    reviewer = Principal("human", "bob")
    new = job_factory(
        tenant_id="acme", workspace_id="ws-core", principal=owner, clock=clock
    )
    deadline = start + timedelta(minutes=5)

    lapsed = new("job-009")
    lapsed.submit_for_governance()
    lapsed.approve(authority=reviewer, reason="approved", expires_at=deadline)
    now["t"] = deadline + timedelta(hours=1)   # nobody came for the job in time
    try:
        lapsed.transition(JobState.TASK_PLANNING)
        fail("approval", "งานเดินเข้า TASK_PLANNING ได้ด้วย approval ที่หมดอายุแล้ว")
    except ExpiredApproval:
        ok("approval", "approval ที่หมดอายุใช้เดินงานต่อไม่ได้ — engine ปฏิเสธ")

    # The job is not stuck: RFC-0007 Amendment 1 gives it somewhere honest to land.
    lapsed.time_out(reason="approval_expired — the approval lapsed before planning began")
    if lapsed.state is JobState.TIMED_OUT:
        ok("approval", "งานที่ถือ approval หมดอายุเข้า TIMED_OUT ได้ (rfcs/0007 Amendment 1)")
    else:
        fail("approval", f"งานที่ถือ approval หมดอายุไปจบที่ {lapsed.state.value}")

    # Now the log. Same journey with an approval that was still valid, then the
    # recorded deadline moved into the past — which is what "the job kept going on
    # an expired approval" looks like when it is read back rather than watched.
    ran_on = new("job-010")
    ran_on.submit_for_governance()
    ran_on.approve(
        authority=reviewer, reason="approved", expires_at=now["t"] + timedelta(hours=1)
    )
    ran_on.transition(JobState.TASK_PLANNING)
    forged = [
        dataclasses.replace(
            event,
            metadata={
                **event.metadata,
                "approval": {
                    **event.metadata["approval"],
                    "expires_at": start.isoformat(),
                },
            },
        )
        if event.type_value == "GOVERNANCE_DECISION"
        else event
        for event in ran_on.events
    ]
    try:
        replay_job(forged)
        fail("approval", "trail ที่บันทึกว่างานเดินต่อหลัง approval หมดอายุ ยัง replay ผ่าน")
    except ExecutionAfterExpiry:
        ok("approval", "replay จับได้ว่า trail บันทึกการเดินงานหลัง approval หมดอายุ")


def check_gap_expiry(gaps: list[dict], today: str) -> None:
    """A waiver with no end date is a permanent exception, which ADR-0006 forbids."""
    for gap in gaps or ():
        expires = str(gap.get("expires") or "")
        if not expires:
            fail("gap", f"{gap['id']} ไม่มีวันหมดอายุ — ADR-0006 ห้ามยกเว้นถาวร")
        elif expires <= today:
            fail(
                "gap",
                f"{gap['id']} หมดอายุแล้ว ({expires}) — ต้องตามที่ {gap['issue']} "
                f"หรือขยายเวลาโดยมีเหตุผลบันทึกไว้",
            )
        else:
            ok("gap", f"{gap['id']} ยังอยู่ในอายุ ถึง {expires} · {gap['issue']}")


def check_guarantees(log, jobs, external) -> None:
    # append-only: the digest of a prefix must not change as the log grows
    before = log.digest("acme")
    log.append(
        __import__("devfactory_observability").accept_external(
            {
                "event_type": "SIGHTING_RECORDED",
                "tenant_id": "acme",
                "subject_type": "external",
                "subject_id": "sighting-4472",
                "source": {"system": "navi-ims"},
            }
        )
    )
    after = log.digest("acme")
    if after == before:
        fail("guarantee", "append ไม่ทำให้ digest เปลี่ยน — chain ไม่ได้ผูกกับเนื้อหา")
    else:
        ok("guarantee", "append-only — digest ผูกกับลำดับที่เขียนจริง")

    # no silent state change
    mismatched = [
        job.job_id
        for job in jobs
        if len(
            [e for e in job.events if e.type_value == "STATE_TRANSITION"]
        )
        != len(job.history)
    ]
    if mismatched:
        fail("guarantee", f"transition ไม่ครบ event: {mismatched}")
    else:
        ok("guarantee", "no silent state change — ทุก transition มี event ตรงจำนวน")

    # every event answers what it is about
    subjectless = [
        p["event_id"]
        for tenant in log.tenants()
        for p in log.payloads(tenant)
        if not p.get("subject_type") or not p.get("subject_id")
    ]
    if subjectless:
        fail("guarantee", f"event ที่ไม่มี subject: {subjectless}")
    else:
        ok("guarantee", "ทุก event ตอบได้ว่าเกี่ยวกับอะไร")

    # our own events always carry job_id; external ones must not have one invented
    ours_missing = [
        e.event_id for job in jobs for e in job.events if e.job_id is None
    ]
    invented = [e.event_id for e in external if e.job_id is not None]
    if ours_missing:
        fail("guarantee", f"event ของเราที่ไม่มี job_id: {ours_missing}")
    elif invented:
        fail("guarantee", f"external event ที่ถูกใส่ job_id ให้: {invented}")
    else:
        ok(
            "guarantee",
            "job_id ครบทุก event ของเรา และไม่ถูกสร้างขึ้นให้ event ภายนอก",
        )

    # a tenant that cannot be resolved is refused rather than defaulted
    from devfactory_observability import MissingTenant, accept_external

    try:
        accept_external(
            {
                "event_type": "SIGHTING_RECORDED",
                "subject_type": "external",
                "subject_id": "sighting-x",
                "source": {"system": "navi-ims"},
            }
        )
        fail("guarantee", "event ที่ไม่มี tenant ถูกรับเข้า — ต้อง reject")
    except MissingTenant:
        ok("guarantee", "event ที่ resolve tenant ไม่ได้ ถูก reject ที่ intake")

    # external stays external, forever
    kinds = {
        p["source"]["kind"]
        for tenant in log.tenants()
        for p in log.payloads(tenant)
    }
    if kinds != {"internal", "external"}:
        fail("guarantee", f"source.kind ที่พบ: {sorted(kinds)} — คาดว่าต้องมีทั้งสอง")
    else:
        ok("guarantee", "external event คง source ไว้ แยกออกจาก internal ได้")

    # no reasoning traces in an audit record
    suspects = ("reasoning", "chain_of_thought", "thoughts", "scratchpad", "prompt")
    leaked = [
        p["event_id"]
        for tenant in log.tenants()
        for p in log.payloads(tenant)
        if any(key in p.get("metadata", {}) for key in suspects)
    ]
    if leaked:
        fail("guarantee", f"metadata มี reasoning trace: {leaked}")
    else:
        ok("guarantee", "ไม่มี private reasoning ใน audit record")

    # tenant isolation is storage-level, not a filter
    for tenant in log.tenants():
        foreign = [p["event_id"] for p in log.payloads(tenant) if p["tenant_id"] != tenant]
        if foreign:
            fail("guarantee", f"partition {tenant} มี event ของ tenant อื่น: {foreign}")
            return
    ok("guarantee", f"tenant isolation — {len(log.tenants())} partition ไม่ปนกัน")


def main() -> int:
    parser = argparse.ArgumentParser(description="payload conformance check")
    parser.add_argument("--offline", action="store_true", help="ใช้ schema จาก cache")
    parser.add_argument("--json", action="store_true", help="พิมพ์ผลเป็น JSON")
    parser.add_argument(
        "--today",
        default=None,
        help="วันที่ใช้ตรวจอายุของ known_gaps (default: วันนี้)",
    )
    args = parser.parse_args()
    if args.today is None:
        from datetime import date

        args.today = date.today().isoformat()

    pinned = load_pinned()
    print("=" * 70)
    print("PAYLOAD CONFORMANCE CHECK")
    print(f"  agent-platform @ {pinned['commit'][:8]} (pinned {pinned['pinned_at']})")
    print("=" * 70)

    schemas = fetch_schemas(pinned, offline=args.offline)
    validator = build_validator(
        schemas,
        "https://schemas.agent-platform.internal/event/v1/event.schema.yaml",
        pinned["non_schema_keys"],
    )
    approval_id = "https://schemas.agent-platform.internal/approval/v1/approval.schema.yaml"
    approval_validator = build_validator(schemas, approval_id, pinned["non_schema_keys"])
    approval_schema = as_json_schema(schemas[approval_id], pinned["non_schema_keys"])

    log, jobs, external = run_scenario()
    print(f"\n[1] payload ที่ระบบผลิตจริง — {len(log)} event จาก {len(jobs)} job")
    check_payloads(log, validator, pinned.get("known_gaps") or [])
    print("\n[2] คำตัดสินที่ระบบผลิตจริง — approval/v1 (RFC-0002)")
    check_decisions(log, jobs, approval_validator, approval_schema)
    print("\n[3] guarantee ที่ JSON Schema ตรวจไม่ได้")
    check_guarantees(log, jobs, external)
    print("\n[4] ช่องว่างที่รู้ตัว — ต้องมี issue และวันหมดอายุ")
    check_gap_expiry(pinned.get("known_gaps") or [], args.today)

    fails = [f for f in findings if f[0] == "FAIL"]
    print("\n" + "=" * 70)
    print(f"  passed={passed}  FAIL={len(fails)}")
    print("=" * 70)

    if args.json:
        print(json.dumps({"passed": passed, "findings": findings}, ensure_ascii=False, indent=2))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
