#!/usr/bin/env python3
"""Run the five obligations of RFC-0014 against an event store implementation.

RFC-0014 Migration Plan step 3. The point of this file is that the in-memory
``EventLog`` is a **reference** implementation rather than a special case: it
passes the same suite a durable one will have to, so a durable store that
behaves differently fails here instead of being discovered in production.

That only holds if the suite tests the store through the contract and never
reaches inside it. Nothing here touches a private attribute, and every check is
written against the surface declared in ``devfactory_observability.contract``.

Why it refuses to run with an unchecked obligation
--------------------------------------------------
``OBLIGATIONS`` is the written contract. If someone adds a sixth and does not
add a check, the honest outcome is a failure, not a green run over five. So the
suite pairs every obligation with a check by number and fails if one is missing.

Usage::

    python3 conformance/store_contract.py
    python3 conformance/store_contract.py --implementation pkg.module:StoreClass
    python3 conformance/store_contract.py --json
"""

from __future__ import annotations

import argparse
import importlib
import json
import pathlib
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT),
    str(ROOT / "packages" / "core"),
    str(ROOT / "packages" / "observability"),
]

from devfactory_core.events import Event, EventType  # noqa: E402
from devfactory_observability.contract import (  # noqa: E402
    MUTATING_NAMES,
    OBLIGATIONS,
    EventStore,
)

DEFAULT_IMPLEMENTATION = "devfactory_observability.store:EventLog"

findings: list[tuple[str, str, str]] = []
passed = 0


def ok(area: str, message: str) -> None:
    global passed
    passed += 1
    print(f"  ✅ {message}")
    findings.append(("PASS", area, message))


def fail(area: str, message: str) -> None:
    print(f"  ❌ {message}")
    findings.append(("FAIL", area, message))


def event(event_id: str, tenant_id: str, *, subject_id: str = "job-1") -> Event:
    """A minimal valid record. Nothing here depends on its contents."""
    return Event(
        event_id=event_id,
        event_type=EventType.JOB_CREATED,
        tenant_id=tenant_id,
        subject_type="job",
        subject_id=subject_id,
        occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


# ---- obligation 1 — append-only --------------------------------------------


def check_append_only(factory) -> None:
    store = factory()
    area = "obligation-1"

    present = sorted(name for name in MUTATING_NAMES if hasattr(store, name))
    if present:
        fail(area, f"มีเมธอดที่แก้ประวัติได้: {', '.join(present)}")
    else:
        ok(area, "ไม่มีเมธอดที่แก้หรือลบระเบียนได้ — append-only เป็นรูปของ API ไม่ใช่ flag")

    store.append(event("e-1", "acme"))
    record = store.read("acme")[0]
    try:
        record.tenant_id = "other"  # type: ignore[misc]
    except Exception:
        ok(area, "ระเบียนที่อ่านออกมาแก้ไม่ได้ — ผู้อ่านเปลี่ยนประวัติผ่านของที่ยืมไปไม่ได้")
    else:
        fail(area, "ระเบียนที่อ่านออกมาถูกแก้ได้ — ประวัติเปลี่ยนได้โดยไม่ผ่าน append")

    before_count, before_digest = store.count("acme"), store.digest("acme")
    try:
        store.append(event("e-1", "acme"))
    except Exception:
        pass
    if (store.count("acme"), store.digest("acme")) == (before_count, before_digest):
        ok(area, "append ที่ถูกปฏิเสธไม่เขียนอะไรเลย")
    else:
        fail(area, "append ที่ถูกปฏิเสธยังทิ้งร่องรอยไว้")


# ---- obligation 2 — tenant isolation ---------------------------------------


def check_tenant_isolation(factory) -> None:
    store = factory()
    area = "obligation-2"

    store.extend([event("a-1", "acme"), event("a-2", "acme")])
    store.append(event("g-1", "globex"))

    if [e.tenant_id for e in store.read("acme")] == ["acme", "acme"]:
        ok(area, "อ่าน tenant หนึ่งไม่เห็นระเบียนของอีก tenant")
    else:
        fail(area, "การอ่านข้าม tenant ได้")

    try:
        empty = store.read("never-existed")
    except Exception as exc:
        fail(area, f"tenant ที่ไม่มีอยู่ทำให้เกิด {type(exc).__name__} — การอ่านตอบว่ามีหรือไม่มี")
    else:
        if empty == ():
            ok(area, "tenant ที่ไม่มีอยู่อ่านได้ว่าว่าง ไม่ใช่ error — การถามไม่ตอบว่ามีอะไรอยู่")
        else:
            fail(area, "tenant ที่ไม่มีอยู่คืนค่าอะไรบางอย่าง")

    # การปฏิเสธก็เป็นคำตอบ — ถ้า id ที่ tenant อื่นถืออยู่ถูกปฏิเสธที่ tenant ว่าง
    # ผู้เรียกจะรู้ว่า id นั้นมีอยู่ที่ไหนสักแห่ง ทั้งที่อ่านไม่เห็น
    try:
        store.append(event("a-1", "globex"))
    except Exception as exc:
        fail(
            area,
            f"id ที่ tenant อื่นถืออยู่ถูกปฏิเสธที่ tenant นี้ ({type(exc).__name__}) "
            "— การปฏิเสธบอกว่าอีก tenant มีอะไร",
        )
    else:
        ok(area, "id เดียวกันใน tenant ต่างกันรับได้ — การปฏิเสธไม่ใช่ช่องถาม")

    listed = store.tenants()
    if all(isinstance(t, str) for t in listed) and set(listed) >= {"acme", "globex"}:
        ok(area, "tenants() คืนเฉพาะรหัส ไม่คืนอะไรจากข้างใน partition")
    else:
        fail(area, "tenants() คืนของที่ไม่ใช่รหัส tenant")

    if all(isinstance(t, str) for t in iter(store)):
        ok(area, "การวนซ้ำ store ให้รหัส tenant ไม่ใช่ระเบียน — ไม่มีการอ่านแบบข้าม tenant")
    else:
        fail(area, "การวนซ้ำ store ให้ระเบียนข้าม tenant")


# ---- obligation 3 — order ---------------------------------------------------


def check_append_order(factory) -> None:
    store = factory()
    area = "obligation-3"

    # สลับกันเขียนสอง tenant เพื่อให้ลำดับของแต่ละฝั่งเป็นของตัวเองจริง ๆ
    # ไม่ใช่ผลพลอยได้จากการเขียนทีละ tenant
    for i in range(1, 4):
        store.append(event(f"a-{i}", "acme"))
        store.append(event(f"g-{i}", "globex"))

    if [e.event_id for e in store.read("acme")] == ["a-1", "a-2", "a-3"]:
        ok(area, "อ่านกลับมาได้ลำดับเดียวกับที่เขียน แม้มี tenant อื่นเขียนคั่น")
    else:
        fail(area, "ลำดับที่อ่านกลับมาไม่ตรงกับลำดับที่เขียน")

    trail = store.read("acme")
    if len(trail) == store.count("acme") == 3 and len({id(e) for e in trail}) == 3:
        ok(area, "ลำดับเป็นระเบียบสมบูรณ์ — ไม่มีสองระเบียนอยู่ตำแหน่งเดียวกัน")
    else:
        fail(area, "จำนวนระเบียนกับลำดับไม่ตรงกัน")

    if [e.event_id for e in store.read("acme", subject_id="job-1")] == ["a-1", "a-2", "a-3"]:
        ok(area, "การกรองไม่เปลี่ยนลำดับ")
    else:
        fail(area, "การกรองเปลี่ยนลำดับ")


# ---- obligation 4 — idempotent by event_id within a tenant ------------------


def check_idempotent(factory) -> None:
    store = factory()
    area = "obligation-4"

    store.append(event("e-1", "acme"))
    try:
        store.append(event("e-1", "acme"))
    except Exception:
        ok(area, "เขียน id เดิมซ้ำใน tenant เดิมถูกปฏิเสธ")
    else:
        fail(area, "เขียน id เดิมซ้ำแล้วได้ระเบียนเพิ่ม — อ่านซ้ำจะทำให้ log บวม")

    if store.count("acme") == 1:
        ok(area, "การปฏิเสธไม่ทิ้งสำเนาไว้")
    else:
        fail(area, f"หลังปฏิเสธ tenant มี {store.count('acme')} ระเบียน")

    # เนื้อหาต่างแต่ id เดิม ต้องถูกปฏิเสธเหมือนกัน — ไม่งั้น id ไม่ได้ระบุระเบียน
    try:
        store.append(event("e-1", "acme", subject_id="job-2"))
    except Exception:
        ok(area, "id เดิมที่เนื้อหาต่างก็ถูกปฏิเสธ — id ระบุระเบียน ไม่ใช่แค่คู่กับเนื้อหา")
    else:
        fail(area, "id เดิมที่เนื้อหาต่างถูกรับ — id ไม่ได้ระบุระเบียนอีกต่อไป")


# ---- obligation 5 — digest --------------------------------------------------


def check_digest(factory) -> None:
    area = "obligation-5"
    store = factory()
    store.extend([event("e-1", "acme"), event("e-2", "acme")])

    if store.digest("acme") == store.digest("acme"):
        ok(area, "digest เดิมซ้ำได้เมื่อไม่มีอะไรเปลี่ยน")
    else:
        fail(area, "digest เปลี่ยนทั้งที่ไม่มีอะไรเปลี่ยน")

    before = store.digest("acme")
    store.append(event("e-3", "acme"))
    if store.digest("acme") != before:
        ok(area, "digest เปลี่ยนเมื่อมีระเบียนเพิ่ม")
    else:
        fail(area, "digest ไม่เปลี่ยนเมื่อมีระเบียนเพิ่ม")

    if factory_digest(factory, [("e-1", "acme"), ("e-2", "acme")]) == factory_digest(
        factory, [("e-1", "acme"), ("e-2", "acme")]
    ):
        ok(area, "digest ของประวัติเดียวกันตรงกันข้าม store — เป็นค่าของประวัติ ไม่ใช่ของ instance")
    else:
        fail(area, "store สองตัวที่มีประวัติเดียวกันได้ digest ต่างกัน")

    missing_middle = factory_digest(factory, [("e-1", "acme"), ("e-3", "acme")])
    full = factory_digest(factory, [("e-1", "acme"), ("e-2", "acme"), ("e-3", "acme")])
    if missing_middle != full:
        ok(area, "ประวัติที่ขาดระเบียนกลางได้ digest ต่างออกไป — การหายตรวจจับได้")
    else:
        fail(area, "ประวัติที่ขาดระเบียนกลางได้ digest เดิม — การหายตรวจจับไม่ได้")

    forward = factory_digest(factory, [("e-1", "acme"), ("e-2", "acme")])
    backward = factory_digest(factory, [("e-2", "acme"), ("e-1", "acme")])
    if forward != backward:
        ok(area, "digest ผูกกับลำดับ ไม่ใช่แค่เซตของระเบียน")
    else:
        fail(area, "digest ไม่ผูกกับลำดับ — การสลับลำดับตรวจจับไม่ได้")

    isolated = factory()
    isolated.append(event("e-1", "acme"))
    quiet = isolated.digest("acme")
    isolated.append(event("g-1", "globex"))
    if isolated.digest("acme") == quiet:
        ok(area, "การเขียนของ tenant อื่นไม่ขยับ digest ของ tenant นี้")
    else:
        fail(area, "digest ของ tenant หนึ่งขยับเพราะอีก tenant เขียน")


def factory_digest(factory, records: list[tuple[str, str]]) -> str:
    store = factory()
    store.extend([event(eid, tenant) for eid, tenant in records])
    return store.digest(records[0][1])


CHECKS = {
    1: check_append_only,
    2: check_tenant_isolation,
    3: check_append_order,
    4: check_idempotent,
    5: check_digest,
}


def resolve(spec: str):
    """Load ``module:Attr`` and return it. Any callable returning a fresh store."""
    module_name, _, attr = spec.partition(":")
    if not attr:
        raise SystemExit(f"--implementation ต้องเป็นรูป module:Attr — ได้ {spec!r}")
    return getattr(importlib.import_module(module_name), attr)


def main() -> int:
    parser = argparse.ArgumentParser(description="event store contract conformance — RFC-0014")
    parser.add_argument(
        "--implementation",
        default=DEFAULT_IMPLEMENTATION,
        help=f"module:Attr ของ store ที่จะตรวจ (default: {DEFAULT_IMPLEMENTATION})",
    )
    parser.add_argument("--json", action="store_true", help="พิมพ์ผลเป็น JSON")
    args = parser.parse_args()

    factory = resolve(args.implementation)

    print("=" * 70)
    print("EVENT STORE CONTRACT — RFC-0014")
    print(f"  implementation: {args.implementation}")
    print("=" * 70)

    unchecked = [o for o in OBLIGATIONS if o.number not in CHECKS]
    if unchecked:
        for obligation in unchecked:
            fail("suite", f"ข้อ {obligation.number} ({obligation.title}) ไม่มีการตรวจ")
        print("\nสัญญาโตเกินกว่าที่ตรวจจริง — หยุดก่อนที่จะรายงานผ่าน")
        return 1

    print("\n[0] surface ที่ประกาศไว้")
    probe = factory()
    if isinstance(probe, EventStore):
        ok("surface", "implementation มีเมธอดครบตาม EventStore")
    else:
        missing = [
            name
            for name in ("append", "extend", "read", "payloads", "tenants", "count", "digest")
            if not hasattr(probe, name)
        ]
        fail("surface", f"ขาดเมธอดตาม EventStore: {', '.join(missing) or '—'}")

    for obligation in OBLIGATIONS:
        print(f"\n[{obligation.number}] {obligation.title} — {obligation.source}")
        CHECKS[obligation.number](factory)

    fails = [f for f in findings if f[0] == "FAIL"]
    print("\n" + "=" * 70)
    print(f"  passed={passed}  FAIL={len(fails)}")
    print("=" * 70)

    if args.json:
        print(json.dumps({"passed": passed, "findings": findings}, ensure_ascii=False, indent=2))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
