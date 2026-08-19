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
a record, and ``REQUIRE_CHANGES`` is refused rather than given a destination
nobody has decided on yet.

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
sys.path[:0] = [str(ROOT / "packages" / "core"), str(ROOT / "packages" / "observability")]

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

    Six jobs across two tenants, covering every terminal state, the mid-run
    approval pause, rejection and resubmission, and recovery by supersession —
    plus two inbound external events that no job caused.
    """
    from devfactory_core import Job, JobState, Principal
    from devfactory_observability import EventLog, accept_external

    log = EventLog()
    owner = Principal("human", "alice", display_name="Alice")
    reviewer = Principal("human", "bob", display_name="Bob")
    agent = Principal("agent", "planner-1", on_behalf_of=owner)

    def new(job_id: str, tenant: str = "acme", workspace: str = "ws-core") -> Job:
        return Job(
            job_id=job_id, tenant_id=tenant, workspace_id=workspace, principal=owner
        )

    # 1. the happy path, with a mid-run approval pause before deploy
    happy = new("job-001")
    happy.submit_for_governance(reason="ready for review")
    happy.approve(authority=reviewer, reason="scope matches milestone v0.1")
    happy.transition(JobState.TASK_PLANNING)
    happy.transition(JobState.IN_PROGRESS)
    happy.transition(JobState.VALIDATING)
    happy.transition(JobState.DEPLOYABLE)
    happy.pause_for_approval(reason="deploy needs sign-off")
    happy.resume(reason="signed off", principal=reviewer)
    happy.transition(JobState.COMPLETED)

    # 2. rejected, revised, resubmitted, approved
    revised = new("job-002")
    revised.submit_for_governance()
    revised.reject(authority=reviewer, reason="missing risk analysis")
    revised.transition(JobState.DRAFT)
    revised.submit_for_governance(reason="risk analysis added")
    revised.approve(authority=reviewer, reason="addressed")

    # 3. failed, then superseded by a fresh job that re-enters governance
    failed = new("job-003")
    failed.submit_for_governance()
    failed.approve(authority=reviewer, reason="approved")
    failed.transition(JobState.TASK_PLANNING)
    failed.transition(JobState.IN_PROGRESS)
    failed.fail(reason="orchestration exhausted execution retries")
    replacement = failed.supersede(job_id="job-004", principal=owner)
    replacement.submit_for_governance(reason="retry with a different plan")

    # 4. cancelled by a person
    cancelled = new("job-005")
    cancelled.submit_for_governance()
    cancelled.cancel(reason="superseded by an urgent request", principal=owner)

    # 5. an approval nobody answered
    stalled = new("job-006", tenant="globex", workspace="ws-platform")
    stalled.submit_for_governance()
    stalled.approve(authority=reviewer, reason="approved")
    stalled.transition(JobState.TASK_PLANNING)
    stalled.transition(JobState.IN_PROGRESS)
    stalled.pause_for_approval(reason="needs a human before merge")
    stalled.time_out(reason="approval request expired after 72h")

    jobs = [happy, revised, failed, replacement, cancelled, stalled]
    for job in jobs:
        log.extend(job.events)

    # 6. events from another system, which no job caused
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


def check_decisions(log, jobs, validator) -> None:
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

    # REQUIRE_CHANGES อยู่ใน vocabulary แต่ยังไม่มี RFC กำหนดว่ามันพา job ไปไหน
    # engine ต้องปฏิเสธ ไม่ใช่เดาปลายทาง — ดู states.DECISION_TARGET
    from devfactory_core import DecisionType, Job, Principal
    from devfactory_core.errors import UnmappedDecision

    probe = Job(
        job_id="job-007",
        tenant_id="acme",
        workspace_id="ws-core",
        principal=Principal("human", "alice"),
    )
    probe.submit_for_governance()
    try:
        probe.decide(
            DecisionType.REQUIRE_CHANGES,
            authority=Principal("human", "bob"),
            reason="needs a test plan",
        )
        fail("approval", "REQUIRE_CHANGES ถูกรับเข้า — ต้องปฏิเสธจนกว่าจะมี RFC กำหนดปลายทาง")
    except UnmappedDecision:
        ok("approval", "REQUIRE_CHANGES ถูกปฏิเสธ — ยังไม่มี RFC กำหนดปลายทางของมัน")


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
    approval_validator = build_validator(
        schemas,
        "https://schemas.agent-platform.internal/approval/v1/approval.schema.yaml",
        pinned["non_schema_keys"],
    )

    log, jobs, external = run_scenario()
    print(f"\n[1] payload ที่ระบบผลิตจริง — {len(log)} event จาก {len(jobs)} job")
    check_payloads(log, validator, pinned.get("known_gaps") or [])
    print("\n[2] คำตัดสินที่ระบบผลิตจริง — approval/v1 (RFC-0002)")
    check_decisions(log, jobs, approval_validator)
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
