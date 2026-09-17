#!/usr/bin/env python3
"""Report how far the pinned contracts have fallen behind upstream.

The gap this closes was measured, not imagined: the pin sat on one commit for 29
days while ``agent-platform`` moved 34 commits ahead, and nothing anywhere said
so. Three checks were running the whole time and none of them could:

* their ``drift_check`` asks whether ``semantics_version`` agrees — it did
* our ``payload_check`` asks whether payloads match the pinned schemas — they did
* our ``push`` and ``pull_request`` workflows fire on changes *here*, and the
  thing that changed was in another repository

So this runs on a schedule, for the same reason ``agent-platform``'s drift check
does: the event worth noticing is one that happens while nothing here moves.

**Falling behind is not itself a fault.** A pin that stays put because upstream
has not moved is a correct pin. What this reports is *behind and left there* —
which means real payloads are being validated against an old copy of the
contract, and nobody would find out until someone moved the pin by hand.

Thresholds live in ``pinned.yaml`` rather than here, so the rule is declared where
a reader of the manifest can see it — the same reason RFC-0013 requires a
declaration to be the artefact its checker reads.

Usage::

    python3 conformance/pin_freshness.py
    python3 conformance/pin_freshness.py --json
    python3 conformance/pin_freshness.py --today 2026-12-01   # for testing thresholds
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.error
import urllib.request
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parents[1]
PINNED = ROOT / "conformance" / "pinned.yaml"
COMPARE = "https://api.github.com/repos/{repo}/compare/{base}...{head}"

OK, WARN, FAIL = "ok", "WARN", "FAIL"


def load_pinned() -> dict:
    import yaml

    return yaml.safe_load(PINNED.read_text(encoding="utf-8"))


def compare_with_upstream(repo: str, commit: str, ref: str) -> dict:
    """Ask GitHub how far the pinned commit is behind the upstream ref.

    Unauthenticated is fine — this is one public read per run, well inside the
    anonymous rate limit, and the workflow passes a token anyway.
    """
    url = COMPARE.format(repo=repo, base=commit, head=ref)
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed host
        return json.loads(response.read().decode("utf-8"))


def assess(pinned: dict, behind: int, age_days: int) -> tuple[str, str]:
    """Decide the verdict. Behind-ness alone never fails; staleness with it does."""
    rules = pinned.get("freshness") or {}
    warn_after = int(rules.get("warn_after_days", 14))
    fail_after = int(rules.get("fail_after_days", 60))
    unknown_after = int(rules.get("adr_0006_unknown_after_days", 90))

    if behind == 0:
        return OK, f"pin ตรงกับ {pinned['ref']} ของต้นทาง — ไม่มีอะไรตามหลัง (อายุ {age_days} วัน)"

    tail = (
        f"ต้นทางขยับไป {behind} commit และ pin ตั้งไว้ {age_days} วันแล้ว "
        f"— conformance กำลัง validate payload จริงกับสำเนาสัญญาเก่า"
    )
    if age_days >= fail_after:
        left = unknown_after - age_days
        urgency = (
            f"เหลืออีก {left} วันก่อนถึง {unknown_after} วันที่ ADR-0006 ถือว่า last_verified "
            f"หมดอายุและสถานะ conforming ของเราจะกลายเป็น unknown เอง"
            if left > 0
            else f"เลย {unknown_after} วันของ ADR-0006 แล้ว — สถานะ conforming ไม่มีผลอีกต่อไป"
        )
        return FAIL, f"{tail} · {urgency}"
    if age_days >= warn_after:
        return WARN, f"{tail} · ยังไม่ถึงเกณฑ์แดงที่ {fail_after} วัน"
    return OK, f"ต้นทางขยับไป {behind} commit แต่ pin เพิ่งตั้งเมื่อ {age_days} วันก่อน"


def main() -> int:
    parser = argparse.ArgumentParser(description="pin freshness check")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--today", default=None, help="วันที่ใช้คำนวณอายุ (ใช้ตอนทดสอบเกณฑ์)")
    args = parser.parse_args()

    today = date.fromisoformat(args.today) if args.today else date.today()
    pinned = load_pinned()
    pinned_at = date.fromisoformat(str(pinned["pinned_at"]))
    age_days = (today - pinned_at).days

    print("=" * 70)
    print("PIN FRESHNESS")
    print(f"  {pinned['repo']} @ {pinned['commit'][:8]} · ตั้งเมื่อ {pinned_at} ({age_days} วันก่อน)")
    print("=" * 70)

    try:
        comparison = compare_with_upstream(pinned["repo"], pinned["commit"], pinned["ref"])
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        # Not a verdict. A check that turns red when GitHub is unreachable teaches
        # people to ignore it, which costs more than the signal is worth.
        print(f"\n  WARN  ถามต้นทางไม่ได้: {exc}")
        print("        ไม่ใช่คำตัดสิน — ลองใหม่รอบหน้า")
        return 0

    behind = int(comparison.get("ahead_by", 0))
    head = (comparison.get("commits") or [{}])[-1].get("sha", "")
    verdict, message = assess(pinned, behind, age_days)

    print(f"\n  {verdict:<4}  {message}")
    if behind:
        print(f"        main ของต้นทางตอนนี้: {head[:8] or '?'}")
        print("        bump เป็น PR แยกเสมอตาม ADR-0006 — อย่าปนกับงาน feature")

    if args.json:
        print(json.dumps(
            {"verdict": verdict, "behind": behind, "age_days": age_days,
             "pinned": pinned["commit"], "upstream": head},
            ensure_ascii=False, indent=2,
        ))
    return 1 if verdict == FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
