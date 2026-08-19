#!/usr/bin/env python3
"""End-to-end flow simulation — issue #7, the closing gate on milestone v0.1.

Drives real jobs through the real state machine, collects what they emit into the
real audit log, and then checks the six things issue #7 asks for. Nothing is
hand-written to make a check pass: every assertion below is made against records
the engine actually wrote.

The six checks map one-to-one onto the issue's list::

    [1] the full flow, DRAFT → … → COMPLETED
    [2] rejection, revision, resubmission
   [2b] being sent back for changes — the same destination by a different route,
        which is what RFC-0011 rests on and so is checked rather than asserted
    [3] failure, from every state RFC-0010 allows and from nowhere else
   [3c] an approval that ran out, and the asking-again that links back to it —
        the other half of ``approval/v1``'s "ต้องขอใหม่", which RFC-0007
        Amendment 2 made expressible (issue #21)
    [4] the governance gate — no execution without an APPROVE *record*
    [5] every transition leaves an audit event
    [6] the trail is complete and replays to the state the job is really in

The seventh item is this file: a runnable script, per the issue. The same checks
also run under pytest — see ``simulation/tests/test_e2e_flow.py`` — so CI fails on
them without anyone having to remember to run this by hand.

Usage::

    python3 simulation/e2e_flow.py           # run the simulation
    python3 simulation/e2e_flow.py --json    # machine-readable result
    python3 simulation/e2e_flow.py --trail   # also print the audit trail it produced

Style follows ``conformance/payload_check.py``, which got here first: findings
printed as they are found, a non-zero exit if any of them failed.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT),
    str(ROOT / "packages" / "core"),
    str(ROOT / "packages" / "observability"),
]

from devfactory_core import Job, JobState, Principal  # noqa: E402
from devfactory_core.errors import (  # noqa: E402
    ExecutionBeforeApproval,
    InvalidTransition,
    JobStateMachineError,
    TerminalState,
)
from devfactory_core.states import (  # noqa: E402
    DECISION_BY_EDGE,
    FAILABLE,
    POST_APPROVAL,
    TERMINAL,
)
from devfactory_observability import (  # noqa: E402
    BrokenTrail,
    EventLog,
    UnauditedDecision,
    UndeclaredTransition,
    replay_job,
    replay_tenant,
)
from simulation.flows import (  # noqa: E402
    MAIN_LINE,
    approval_expired_then_resubmitted,
    cancelled_by_a_person,
    failed_at,
    happy_path,
    job_factory,
    never_approved,
    rejected_then_resubmitted,
    require_changes_then_resubmitted,
    stalled_awaiting_approval,
)

TENANT = "acme"
WORKSPACE = "ws-core"

#: The flow issue #7 spells out, kept here as the thing to compare *against* the
#: table. It is written down once, in the assertion, and never used to drive
#: anything — the simulation walks ``MAIN_LINE``, which is read out of
#: ``states.py``. If the two ever disagree, check [1] says so instead of the
#: simulation quietly following the issue and reporting success.
ISSUE_7_FLOW = (
    "DRAFT",
    "GOVERNANCE_ANALYSIS",
    "APPROVED",
    "TASK_PLANNING",
    "IN_PROGRESS",
    "VALIDATING",
    "DEPLOYABLE",
    "COMPLETED",
)

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


def check(area: str, condition: bool, when_ok: str, when_bad: str) -> bool:
    (ok if condition else fail)(area, when_ok if condition else when_bad)
    return condition


def monotonic_clock():
    """A fake clock, so the trail's ordering does not depend on wall time."""
    start = datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc)
    state = {"n": 0}

    def tick() -> datetime:
        state["n"] += 1
        return start + timedelta(seconds=state["n"])

    return tick


def visited(job: Job) -> tuple[str, ...]:
    """The states the job was actually in, read from its own history."""
    return (JobState.DRAFT.value,) + tuple(h.to_state.value for h in job.history)


# ---- [1] the full flow ------------------------------------------------------


def check_full_flow(new, reviewer) -> Job:
    if [s.value for s in MAIN_LINE] != list(ISSUE_7_FLOW):
        fail(
            "flow",
            f"เส้นทางที่ตารางประกาศ {[s.value for s in MAIN_LINE]} ไม่ตรงกับที่ #7 ระบุ "
            f"{list(ISSUE_7_FLOW)} — ถ้าตารางถูก ต้องแก้ issue ไม่ใช่แก้ simulation",
        )
    else:
        ok("flow", f"เส้นทางเดินหน้าอ่านจาก states.py ได้ตรงกับ #7 ทั้ง {len(MAIN_LINE)} state")

    job = happy_path(new, job_id="job-001", authority=reviewer)
    check(
        "flow",
        visited(job) == ISSUE_7_FLOW,
        f"job-001 เดินครบเส้นทาง {' → '.join(ISSUE_7_FLOW)}",
        f"job-001 เดินได้ {visited(job)}",
    )
    check(
        "flow",
        job.state is JobState.COMPLETED and job.is_terminal,
        "จบที่ COMPLETED และเป็น terminal",
        f"จบที่ {job.state.value}",
    )
    check(
        "flow",
        any(e.type_value == "JOB_COMPLETED" for e in job.events),
        "COMPLETED มี JOB_COMPLETED กำกับ",
        "ถึง COMPLETED แต่ไม่มี JOB_COMPLETED",
    )
    return job


# ---- [2] rejection and resubmission -----------------------------------------


def check_rejection_flow(new, reviewer) -> Job:
    job = rejected_then_resubmitted(new, job_id="job-002", authority=reviewer)
    expected = (
        "DRAFT",
        "GOVERNANCE_ANALYSIS",
        "REJECTED",
        "DRAFT",
        "GOVERNANCE_ANALYSIS",
        "APPROVED",
        "TASK_PLANNING",
    )
    check(
        "reject",
        visited(job) == expected,
        f"job-002 เดิน {' → '.join(expected)}",
        f"job-002 เดินได้ {visited(job)}",
    )

    rejection, approval = job.decisions[0], job.decisions[-1]
    check(
        "reject",
        rejection.decision.value == "REJECT" and approval.decision.value == "APPROVE",
        "ยื่นใหม่แล้วได้ decision ใบที่สอง ไม่ใช่การแก้ใบเดิม",
        f"decision ที่บันทึกคือ {[d.decision.value for d in job.decisions]}",
    )
    check(
        "reject",
        approval.supersedes_decision_id == rejection.decision_id,
        "APPROVE ใบใหม่อ้างถึง REJECT ใบเดิม (approval/v1)",
        "APPROVE ใบใหม่ไม่ได้อ้างใบเดิม",
    )
    check(
        "reject",
        job.approval is not None and job.approval.decision_id == approval.decision_id,
        "งานกำลังทำงานภายใต้ APPROVE ใบล่าสุด",
        "งานเดินต่อได้โดยไม่ได้ถือ APPROVE ใบล่าสุด",
    )

    # A rejection must not leave an approval behind — proved on a job that had one.
    approved = new("job-002b")
    approved.submit_for_governance()
    approved.approve(authority=reviewer, reason="first pass")
    approved.transition(JobState.TASK_PLANNING)
    approved.fail(reason="the approved plan does not work")
    replacement = approved.supersede(job_id="job-002c")
    replacement.submit_for_governance(reason="revised plan")
    replacement.reject(authority=reviewer, reason="still wrong")
    replacement.transition(JobState.DRAFT)
    try:
        replacement.transition(JobState.TASK_PLANNING)
        fail("reject", "งานที่ถูก REJECT ยังเดินเข้า TASK_PLANNING ได้")
    except InvalidTransition:
        ok("reject", "REJECT ล้าง approval ทิ้ง — งานที่ยื่นใหม่ยังทำงานไม่ได้จนกว่าจะอนุมัติอีกครั้ง")
    return job


# ---- [2b] sent back for changes ---------------------------------------------
# RFC-0011. Not one of issue #7's six items — the flow did not exist when #7 was
# written — but it belongs beside rejection, because the two end in the same place
# and the whole question is whether they can still be told apart afterwards.


def check_require_changes_flow(new, reviewer) -> Job:
    job = require_changes_then_resubmitted(new, job_id="job-002d", authority=reviewer)
    expected = (
        "DRAFT",
        "GOVERNANCE_ANALYSIS",
        "DRAFT",
        "GOVERNANCE_ANALYSIS",
        "APPROVED",
        "TASK_PLANNING",
    )
    check(
        "changes",
        visited(job) == expected,
        f"job-002d เดิน {' → '.join(expected)}",
        f"job-002d เดินได้ {visited(job)}",
    )
    check(
        "changes",
        [d.decision.value for d in job.decisions] == ["REQUIRE_CHANGES", "APPROVE"],
        "ตีกลับให้แก้แล้วยื่นใหม่ ได้ decision ใบที่สอง ไม่ใช่การแก้ใบเดิม",
        f"decision ที่บันทึกคือ {[d.decision.value for d in job.decisions]}",
    )

    # A REQUIRE_CHANGES leaves no approval behind, exactly as a REJECT does not.
    sent_back = new("job-002e")
    sent_back.submit_for_governance()
    sent_back.require_changes(authority=reviewer, reason="needs a test plan")
    if sent_back.approval is not None:
        fail("changes", "REQUIRE_CHANGES ไม่ได้ล้าง approval — งานที่ยังไม่อนุมัติถือ approval อยู่")
    else:
        try:
            sent_back.transition(JobState.TASK_PLANNING)
            fail("changes", "งานที่ถูกตีกลับให้แก้ยังเดินเข้า TASK_PLANNING ได้")
        except InvalidTransition:
            ok("changes", "REQUIRE_CHANGES ไม่ใช่การอนุมัติ — งานยังทำงานไม่ได้จนกว่าจะได้ APPROVE")

    # 🔑 The claim RFC-0011 rests on: same destination, different trail. Checked by
    # replaying both from the log alone — if a reader who was not there can tell
    # them apart, the invariant survived the shared destination.
    rejected = rejected_then_resubmitted(new, job_id="job-002f", authority=reviewer)
    by_replay = {
        "require_changes": replay_job(job.events),
        "reject": replay_job(rejected.events),
    }
    routes = {
        name: tuple(s.value for s in seen.states_visited)
        for name, seen in by_replay.items()
    }
    check(
        "changes",
        JobState.REJECTED not in by_replay["require_changes"].states_visited
        and JobState.REJECTED in by_replay["reject"].states_visited
        and routes["require_changes"] != routes["reject"],
        "replay แยกสองเคสออกจากกันได้ — เส้นทางของ REQUIRE_CHANGES ไม่ผ่าน REJECTED "
        "ส่วนของ REJECT ผ่าน",
        f"replay แยกไม่ออก: {routes}",
    )
    check(
        "changes",
        [d for d in by_replay["require_changes"].decision_ids]
        == [d.decision_id for d in job.decisions],
        "decision ที่ replay ได้ตรงกับที่ engine บันทึกจริง รวมใบ REQUIRE_CHANGES",
        "decision ที่ replay ได้ไม่ตรงกับของจริง",
    )
    return job


# ---- [3] failure ------------------------------------------------------------


def check_failure_flow(new, reviewer) -> list[Job]:
    jobs: list[Job] = []
    for index, state in enumerate(sorted(FAILABLE, key=lambda s: s.value)):
        reason = f"orchestration exhausted execution retries in {state.value}"
        job = failed_at(
            new,
            job_id=f"job-003-{index}",
            authority=reviewer,
            state=state,
            reason=reason,
        )
        jobs.append(job)
        last = job.history[-1]
        if not (
            job.state is JobState.FAILED
            and last.from_state is state
            and last.reason == reason
        ):
            fail("fail", f"ล้มเหลวจาก {state.value} ไม่ได้บันทึกตามที่เกิดจริง")
            continue
        stored = [
            e
            for e in job.events
            if e.type_value == "STATE_TRANSITION" and e.transition["to"] == "FAILED"
        ]
        check(
            "fail",
            len(stored) == 1 and stored[0].transition.get("reason") == reason,
            f"FAILED จาก {state.value} พร้อมเหตุผลใน audit trail",
            f"FAILED จาก {state.value} แต่เหตุผลไม่ได้ลงใน event",
        )

    refused = sorted(set(JobState) - FAILABLE - TERMINAL, key=lambda s: s.value)
    for state in refused:
        job = new(f"job-003-x-{state.value.lower().replace('_', '-')}")
        _park_in(job, state, reviewer)
        try:
            job.fail(reason="pretending there is work to fail")
            fail("fail", f"{state.value} เข้า FAILED ได้ ทั้งที่ RFC-0010 ไม่อนุญาต")
        except JobStateMachineError:
            pass
    ok(
        "fail",
        f"FAILED เข้าได้เฉพาะ {len(FAILABLE)} state ที่ RFC-0010 อนุญาต "
        f"และถูกปฏิเสธจากอีก {len(refused)} state ({', '.join(s.value for s in refused)})",
    )

    # RFC-0007: recovery is a new job that passes governance again, not a revival.
    origin = jobs[0]
    replacement = origin.supersede(job_id="job-003-next")
    check(
        "fail",
        replacement.state is JobState.DRAFT
        and replacement.supersedes_job_id == origin.job_id,
        "งานที่ล้มเหลวถูกแทนที่ด้วย job ใหม่ที่ชี้กลับไปหาใบเดิม ไม่ใช่การปลุกใบเดิม",
        "supersede ไม่ได้สร้าง job ใหม่ที่ชี้กลับใบเดิม",
    )
    try:
        replacement.transition(JobState.TASK_PLANNING)
        fail("fail", "job ที่มาแทนเดินเข้า TASK_PLANNING ได้โดยไม่ผ่าน governance")
    except InvalidTransition:
        ok("fail", "job ที่มาแทนต้องผ่าน GOVERNANCE_ANALYSIS ใหม่")
    jobs.append(replacement)
    return jobs


def _park_in(job: Job, state: JobState, authority: Principal) -> None:
    """Put a fresh job into ``state``, for the states that cannot fail.

    Only ever called with DRAFT, GOVERNANCE_ANALYSIS, APPROVED, or REJECTED —
    everything RFC-0010 refuses ``FAILED`` from.
    """
    if state is JobState.DRAFT:
        return
    job.submit_for_governance()
    if state is JobState.GOVERNANCE_ANALYSIS:
        return
    if state is JobState.REJECTED:
        job.reject(authority=authority, reason="out of scope")
        return
    job.approve(authority=authority, reason="approved")


# ---- [3c] asking again after an approval ran out ----------------------------
# RFC-0007 Amendment 2 (issue #21). ``approval/v1`` says an expired approval cannot
# run work *and* has to be asked for again. Issue #17 built the refusal; this is the
# asking, and the thing worth checking is that the second attempt is readable as a
# second attempt from the log alone rather than looking like unrelated work.


def check_asking_again(new, reviewer) -> list[Job]:
    expired, again = approval_expired_then_resubmitted(
        new,
        job_id="job-007",
        replacement_job_id="job-007-next",
        authority=reviewer,
        expires_at=datetime(2026, 8, 19, 8, tzinfo=timezone.utc),
    )
    check(
        "again",
        expired.state is JobState.TIMED_OUT and expired.approval_expired,
        "ใบเดิมจบที่ TIMED_OUT เพราะ approval หมดอายุ",
        f"ใบเดิมจบที่ {expired.state.value}",
    )
    check(
        "again",
        again.supersedes_job_id == expired.job_id
        and visited(again)
        == ("DRAFT", "GOVERNANCE_ANALYSIS", "APPROVED", "TASK_PLANNING"),
        "ยื่นใหม่เป็น job ใหม่ที่ชี้กลับใบเดิม เริ่มที่ DRAFT และผ่านประตูอีกครั้ง",
        f"ยื่นใหม่แล้วได้ {visited(again)} ชี้กลับ {again.supersedes_job_id}",
    )
    check(
        "again",
        again.approval is not None
        and expired.approval is not None
        and again.approval.decision_id != expired.approval.decision_id
        and again.approval.expires_at is None,
        "งานที่ยื่นใหม่เดินอยู่ใต้ APPROVE ใบใหม่ ไม่ใช่ใบที่หมดอายุไปแล้ว",
        "งานที่ยื่นใหม่ไม่ได้ถือ APPROVE ใบใหม่",
    )

    # The predecessor is superseded, not revived — the whole reason Decision 1
    # refuses to relax, and the half of it Amendment 2 deliberately did not touch.
    try:
        expired.transition(JobState.TASK_PLANNING)
        fail("again", "ใบเดิมที่ TIMED_OUT ยังเดินต่อได้ — terminal ไม่ terminal จริง")
    except TerminalState:
        ok("again", "ใบเดิมยังเป็น terminal — ยื่นใหม่คือ job ใหม่ ไม่ใช่การปลุกใบเดิม")

    # 🔑 The chain, read back by someone who was not there: from the replacement,
    # follow supersedes_job_id until it runs out, and check where it lands.
    trail = EventLog()
    trail.extend(expired.events)
    trail.extend(again.events)
    seen = replay_tenant(trail, TENANT)
    chain: list[str] = []
    cursor: str | None = again.job_id
    while cursor is not None and cursor not in chain:
        chain.append(cursor)
        cursor = seen[cursor].supersedes_job_id if cursor in seen else None
    check(
        "again",
        chain == [again.job_id, expired.job_id]
        and seen[expired.job_id].state is JobState.TIMED_OUT
        and seen[again.job_id].approval_decision_id
        != seen[expired.job_id].approval_decision_id,
        f"replay อ่านสายโซ่ออกจาก audit log อย่างเดียว: {' → '.join(chain)} "
        f"และเห็นว่าปลายสายจบที่ TIMED_OUT ด้วย approval คนละใบ",
        f"replay อ่านสายโซ่ไม่ครบ: {chain}",
    )

    # COMPLETED is not in SUPERSEDABLE, and the refusal is checked rather than
    # merely written down: a job that delivered is not an attempt awaiting another.
    delivered = happy_path(new, job_id="job-007-done", authority=reviewer)
    try:
        delivered.supersede(job_id="job-007-done-again")
        fail("again", "job ที่ COMPLETED ถูก supersede ได้ — สายโซ่เลิกแปลว่าความพยายาม")
    except InvalidTransition:
        ok("again", "COMPLETED supersede ไม่ได้ — งานที่ส่งมอบแล้วไม่ใช่ความพยายามที่รอลองใหม่")

    still_running = new("job-007-live")
    still_running.submit_for_governance()
    try:
        still_running.supersede(job_id="job-007-live-again")
        fail("again", "job ที่ยังไม่จบถูก supersede ได้")
    except InvalidTransition:
        ok("again", "job ที่ยังไม่ settle supersede ไม่ได้ — ยังไม่มีความพยายามที่ใช้ไปแล้ว")

    return [expired, again, delivered]


# ---- [4] the governance gate ------------------------------------------------


def check_governance_gate(new, reviewer) -> list[Job]:
    job = never_approved(new, job_id="job-004")
    check(
        "gate",
        job.approval is None,
        "งานที่ยังไม่ได้ตัดสิน ไม่ถือ Decision ใด ๆ",
        "งานที่ยังไม่ได้ตัดสินกลับถือ approval อยู่",
    )
    blocked = []
    for target in sorted(POST_APPROVAL, key=lambda s: s.value):
        try:
            job.transition(target)
            fail("gate", f"เข้า {target.value} ได้ทั้งที่ยังไม่มี APPROVE")
        except JobStateMachineError:
            blocked.append(target.value)
    check(
        "gate",
        len(blocked) == len(POST_APPROVAL),
        f"ไม่มี APPROVE แล้วเข้า execution ไม่ได้ทั้ง {len(blocked)} state "
        f"({', '.join(blocked)})",
        "มี state ฝั่ง execution ที่เข้าได้โดยไม่มี APPROVE",
    )

    # The gate is a record, not a flag: forcing the *state* is not enough.
    forced = new("job-004b")
    forced.submit_for_governance()
    forced._state = JobState.APPROVED  # simulating a wrongly edited table
    try:
        forced.transition(JobState.TASK_PLANNING)
        fail("gate", "อยู่ใน APPROVED โดยไม่มี Decision record แล้วยังเดินต่อได้")
    except ExecutionBeforeApproval:
        ok("gate", "gate ผูกกับ Decision record — สถานะ APPROVED เปล่า ๆ เดินต่อไม่ได้")

    # And what authorises execution is a record with an accountable authority.
    approved = new("job-004c")
    approved.submit_for_governance()
    record = approved.approve(authority=reviewer, reason="scope matches milestone v0.1")
    approved.transition(JobState.TASK_PLANNING)
    check(
        "gate",
        approved.approval is record
        and record.authority.id == reviewer.id
        and bool(record.reason)
        and record.decided_at is not None,
        "สิ่งที่อนุญาตให้ execute คือ record ที่ตอบได้ว่าใครตัดสิน ด้วยเหตุผลอะไร เมื่อไร",
        "approval ที่ถืออยู่ไม่ได้ตอบว่าใครตัดสินด้วยเหตุผลอะไร",
    )

    # Read back off the log: strip the decision and the trail no longer holds up.
    tampered = [e for e in approved.events if e.type_value != "GOVERNANCE_DECISION"]
    try:
        replay_job(tampered)
        fail("gate", "trail ที่ไม่มี GOVERNANCE_DECISION ยัง replay ผ่าน")
    except UnauditedDecision:
        ok("gate", "replay ปฏิเสธ trail ที่เข้า APPROVED โดยไม่มี decision บันทึกไว้")

    # ``forced`` is deliberately corrupted and is not filed: an audit log is not
    # the place to keep a job whose state was set behind the engine's back.
    return [job, approved]


# ---- [5] every transition emits an audit event ------------------------------


def check_every_transition_is_audited(jobs, log) -> None:
    silent = [
        job.job_id
        for job in jobs
        if len([e for e in job.events if e.type_value == "STATE_TRANSITION"])
        != len(job.history)
    ]
    check(
        "audit",
        not silent,
        f"ทุก transition ของ {len(jobs)} job มี STATE_TRANSITION ตรงจำนวน "
        f"(รวม {sum(len(j.history) for j in jobs)} transition)",
        f"มี transition ที่ไม่ได้ emit event: {silent}",
    )

    check(
        "audit",
        len(log) == sum(len(job.events) for job in jobs) and log.tenants() == (TENANT,),
        f"ล็อกเก็บครบทุก event ที่ {len(jobs)} job ผลิต ในพาร์ทิชันของ {TENANT} พาร์ทิชันเดียว",
        f"ล็อกมี {len(log)} event แต่ job ผลิตรวม {sum(len(j.events) for j in jobs)}",
    )

    by_id = {e.event_id: e for e in log.read(TENANT)}
    unlinked = [
        (job.job_id, record.from_state.value, record.to_state.value)
        for job in jobs
        for record in job.history
        if record.event_id not in by_id
        or by_id[record.event_id].transition
        != {
            k: v
            for k, v in (
                ("from", record.from_state.value),
                ("to", record.to_state.value),
                ("reason", record.reason),
            )
            if v is not None
        }
    ]
    check(
        "audit",
        not unlinked,
        "ทุก transition ชี้ไปที่ event ที่มีอยู่จริงในล็อก และเนื้อหาตรงกัน",
        f"transition ที่ event หายไปหรือเนื้อหาไม่ตรง: {unlinked}",
    )

    # Which transitions are decisions is read off states.py by *edge*: since
    # RFC-0011 the destination alone no longer says, DRAFT being reachable both by
    # a REQUIRE_CHANGES from the gate and by the revision step out of REJECTED.
    undecided = [
        job.job_id
        for job in jobs
        if len([h for h in job.history if (h.from_state, h.to_state) in DECISION_BY_EDGE])
        != len([e for e in job.events if e.type_value == "GOVERNANCE_DECISION"])
    ]
    check(
        "audit",
        not undecided,
        "ทุก edge ที่เป็นคำตัดสินมี GOVERNANCE_DECISION คู่กับ STATE_TRANSITION "
        "และ edge ที่ไม่ใช่คำตัดสินไม่มี",
        f"job ที่เข้า state ตัดสินใจโดยไม่มี GOVERNANCE_DECISION: {undecided}",
    )


# ---- [6] the trail is complete and replays ----------------------------------


def check_replay(jobs, log) -> None:
    replayed = replay_tenant(log, TENANT)
    live = {job.job_id: job for job in jobs}

    missing = sorted(set(live) - set(replayed))
    if missing:
        fail("replay", f"job ที่ไม่มีร่องรอยในล็อก: {missing}")
        return

    wrong = []
    for job_id, job in sorted(live.items()):
        seen = replayed[job_id]
        expected_approval = job.approval.decision_id if job.approval else None
        if (
            seen.state is not job.state
            or seen.awaiting_from is not job.awaiting_from
            or seen.approval_decision_id != expected_approval
            or seen.approval_expires_at
            != (job.approval.expires_at if job.approval else None)
            or seen.tenant_id != job.tenant_id
            or seen.workspace_id != job.workspace_id
            or seen.supersedes_job_id != job.supersedes_job_id
            or [(t.from_state, t.to_state, t.reason) for t in seen.history]
            != [(h.from_state, h.to_state, h.reason) for h in job.history]
            or [t.decision_id for t in seen.history if t.decision_id]
            != [h.decision_id for h in job.history if h.decision_id]
            or seen.completed is not (job.state is JobState.COMPLETED)
        ):
            wrong.append(f"{job_id} (live={job.state.value} replay={seen.state.value})")
    check(
        "replay",
        not wrong,
        f"replay จาก audit log อย่างเดียว ได้สถานะเดิมครบทั้ง {len(live)} job "
        f"— state, awaiting_from, approval, และประวัติทุกก้าว",
        f"replay แล้วไม่ตรงกับของจริง: {wrong}",
    )

    # A trail is complete only if losing a record is detectable. Drop one and see.
    donor = live["job-001"]
    transitions = [e for e in donor.events if e.type_value == "STATE_TRANSITION"]
    dropped = [e for e in donor.events if e is not transitions[len(transitions) // 2]]
    try:
        replay_job(dropped)
        fail("replay", "ลบ STATE_TRANSITION ออกหนึ่งใบแล้ว replay ยังผ่าน — ล็อกไม่ครบก็ไม่รู้")
    except BrokenTrail:
        ok("replay", "ลบ event ออกหนึ่งใบแล้ว replay จับได้ — ล็อกครบจริงจึง replay ผ่าน")

    # And only if a forged edge is refused rather than followed.
    target = next(
        e
        for e in donor.events
        if e.type_value == "STATE_TRANSITION" and e.transition["from"] == "IN_PROGRESS"
    )
    forged = [
        dataclasses.replace(e, transition={**e.transition, "to": "COMPLETED"})
        if e is target
        else e
        for e in donor.events
    ]
    try:
        replay_job(forged)
        fail("replay", "trail ที่บันทึก IN_PROGRESS → COMPLETED ถูก replay ตามไปด้วย")
    except UndeclaredTransition:
        ok("replay", "replay ตรวจทุก edge กับ states.py — edge ที่ตารางไม่ประกาศถูกปฏิเสธ")


# ---- run --------------------------------------------------------------------


def simulate(log: EventLog) -> list[Job]:
    """Run every flow, file everything in the log, and return the jobs."""
    clock = monotonic_clock()
    owner = Principal("human", "alice", display_name="Alice")
    reviewer = Principal("human", "bob", display_name="Bob")
    new = job_factory(
        tenant_id=TENANT, workspace_id=WORKSPACE, principal=owner, clock=clock
    )

    print(f"\n[1] flow เต็ม — {' → '.join(ISSUE_7_FLOW)}")
    happy = check_full_flow(new, reviewer)

    print("\n[2] flow ปฏิเสธ — REJECTED กลับ DRAFT แล้วยื่นใหม่")
    rejected = check_rejection_flow(new, reviewer)

    print("\n[2b] flow ตีกลับให้แก้ — REQUIRE_CHANGES กลับ DRAFT ตรง ๆ (rfcs/0011)")
    sent_back = check_require_changes_flow(new, reviewer)

    print("\n[3] flow ล้มเหลว — FAILED จาก state ที่ RFC-0010 อนุญาตเท่านั้น")
    failures = check_failure_flow(new, reviewer)

    print("\n[3c] approval หมดอายุแล้วยื่นใหม่ — สายโซ่ต้องไม่ขาด (rfcs/0007 Amendment 2)")
    asked_again = check_asking_again(new, reviewer)

    print("\n[4] governance gate — ไม่มี APPROVE record ก็ไม่มี execution")
    gated = check_governance_gate(new, reviewer)

    # Two flows this repository already exercised in conformance, kept in the run so
    # the trail the replay checks work on covers every terminal state. The third —
    # an approval that ran out where it sat — moved into check [3c], which now walks
    # it to the end: since RFC-0007 Amendment 1 it produces the APPROVED -> TIMED_OUT
    # edge and an approval carrying an expires_at for check [6] to reconstruct, and
    # since Amendment 2 it also produces the supersession link out the other side.
    cancelled = cancelled_by_a_person(new, job_id="job-005", owner=owner)
    stalled = stalled_awaiting_approval(new, job_id="job-006", authority=reviewer)

    jobs = [
        happy,
        rejected,
        sent_back,
        *failures,
        *asked_again,
        *gated,
        cancelled,
        stalled,
    ]
    for job in jobs:
        log.extend(job.events)
    return jobs


def main() -> int:
    parser = argparse.ArgumentParser(description="end-to-end flow simulation (issue #7)")
    parser.add_argument("--json", action="store_true", help="พิมพ์ผลเป็น JSON")
    parser.add_argument("--trail", action="store_true", help="พิมพ์ audit trail ที่ผลิตได้")
    args = parser.parse_args()

    print("=" * 70)
    print("END-TO-END FLOW SIMULATION — issue #7 (v0.1.0)")
    print("=" * 70)

    log = EventLog()
    jobs = simulate(log)

    print(f"\n[5] ทุก transition ต้องมี audit event — {len(log)} event จาก {len(jobs)} job")
    check_every_transition_is_audited(jobs, log)

    print("\n[6] audit log ครบและ replay ได้")
    check_replay(jobs, log)

    if args.trail:
        print("\naudit trail")
        for payload in log.payloads(TENANT):
            move = payload.get("transition")
            arrow = f" {move['from']} → {move['to']}" if move else ""
            print(f"  {payload['occurred_at']}  {payload['event_type']:<20}"
                  f" {payload.get('job_id', '-'):<16}{arrow}")

    fails = [f for f in findings if f[0] == "FAIL"]
    print("\n" + "=" * 70)
    print(f"  passed={passed}  FAIL={len(fails)}")
    print("=" * 70)

    if args.json:
        print(json.dumps({"passed": passed, "findings": findings}, ensure_ascii=False, indent=2))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
