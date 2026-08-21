# conformance

ADR-0006 requirement 2 for a consumer: a test in CI that validates **real payloads**
against the pinned contracts.

```bash
python3 conformance/payload_check.py             # fetch schemas, then validate
python3 conformance/payload_check.py --offline    # reuse the cache, no network
python3 conformance/payload_check.py --json       # machine-readable result
```

## Why real payloads and not a `$ref` check

`care-agent-platform` pinned `event/v1` correctly, had every `$ref` right, and its
first run still found three payloads that did not conform — one of them an `error`
sent as free text where the contract requires the `error/v1` object. **Declared is
not conforming.** That is the whole reason ADR-0006 asks for payloads rather than
pointers, so this file runs the real engine and validates what comes out of it.

## What it does

1. Reads [`pinned.yaml`](pinned.yaml) — the `agent-platform` commit this repository
   conforms to. The schema cache key includes that commit, so bumping the pin cannot
   silently reuse the previous contract's files.
2. Runs a scenario through the real `Job` state machine and the real `EventLog`:
   six jobs across two tenants covering every terminal state, the mid-run approval
   pause, rejection and resubmission, recovery by supersession — plus inbound
   external events that no job caused.
3. Validates every emitted payload against `event/v1`.
4. Checks that those approval payloads use `approval/v1`'s **field names** and not
   names of ours. The schema leaves `additionalProperties` open, so a field we
   invented validates in silence — the contract's own `properties` list is used as
   the closed set the contract does not declare it to be. This is what would have
   caught `supersedes_decision_id` still riding the wire after `approval/v1` v1.1.0
   named the field `supersedes_approval_id`.
5. Asserts the eight `event/v1` guarantees that JSON Schema cannot express:
   append-only, no silent state change, subject always answerable, `job_id` never
   fabricated, unresolvable tenant rejected at intake, external source preserved,
   no reasoning traces in an audit record, tenant partitions not mixed.
6. Checks that every entry in `known_gaps` still has an issue and an unexpired date.

Nothing in the scenario is hand-written to please the schema. If a payload does not
conform, the fix is the code or an upstream issue — never the fixture.

## Non-JSON-Schema keys

Platform schemas carry `derived_from`, `guarantees`, and `platform_rules` at the top
level. Those are how `agent-platform` records provenance and the semantics that may
not change, and they are not JSON Schema, so `pinned.yaml` lists them under
`non_schema_keys` and they are stripped before validation. They are not a defect.

## `known_gaps`

A gap is tolerated only when it names both the JSON path and the kind of event, and
only until its expiry date — ADR-0006 forbids a permanent exception. Any failure
that does not match both conditions turns the run red.

**There is no gap open right now**, and the empty list is the point rather than an
oversight. A waiver left behind after its cause is fixed does not sit there
harmlessly — it keeps swallowing any failure that matches its conditions, so the
next real breakage at the same spot passes silently. That is the same false-green
shape this repository has already been bitten by twice.

The one that used to live here, [`agent-platform#17`](https://github.com/monthop-gmail/agent-platform/issues/17),
was found by this check on its first run: `event/v1` `$defs.EventType` was a closed
enum, contradicting its own `platform_rules` and the RFC-0009 amendment to ADR-0006
Rule 2. It was fixed upstream on 2026-08-18 — `event_type` now refs `EventTypeName`,
an open set constrained by shape — and retired here on 2026-08-21 once the pin
carried the fix and nothing depended on the waiver any more.

Why the gap history is not kept in this file: `known_gaps` is machinery, and a
retired entry left in machinery still runs. The record of what we once deviated on
and why belongs in `platform-contract.yaml` under `gaps:`, which is documentation and
marks closed entries `status: resolved` instead of deleting them.

## Upgrading the pin

Always a separate PR: change `commit` in `pinned.yaml`, run the check, fix whatever
broke, then merge. Never mixed with feature work — otherwise there is no way to tell
whether a failure came from the contract or from the code.
