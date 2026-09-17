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

0. Checks that our own manifests parse and carry the keys their readers depend on.
   Added after `platform-contract.yaml` was left unparseable by an edit and nothing
   here noticed — every other check in this file reads `contract-semantics.yaml`
   and none opens the other manifest, so the first thing to go red would have been
   `agent-platform`'s drift check, which fetches it. A mistake of ours would have
   surfaced as a failure in someone else's repository.
1. Reads [`pinned.yaml`](pinned.yaml) — the `agent-platform` commit this repository
   conforms to. The schema cache key includes that commit, so bumping the pin cannot
   silently reuse the previous contract's files.
2. Runs a scenario through the real `Job` state machine and the real `EventLog`:
   six jobs across two tenants covering every terminal state, the mid-run approval
   pause, rejection and resubmission, recovery by supersession — plus inbound
   external events that no job caused, including the **real `ADVISORY_ISSUED`
   payload from [issue #32](https://github.com/monthop-gmail/devfactory-core/issues/32)
   copied whole**. The two reduced external fixtures that preceded it carried
   `metadata={'record_type': ...}` and no metadata at all, and kept this file green
   while that payload — in the issue since 2026-08-21 — held eight leaves nothing
   had looked at. A fixture smaller than the thing it stands for is how RFC-0013
   passed for a day without covering the case it was written for.
3. Validates every emitted payload against `event/v1`.
4. Checks that those approval payloads use `approval/v1`'s **field names** and not
   names of ours. The schema leaves `additionalProperties` open, so a field we
   invented validates in silence — the contract's own `properties` list is used as
   the closed set the contract does not declare it to be. This is what would have
   caught `supersedes_decision_id` still riding the wire after `approval/v1` v1.1.0
   named the field `supersedes_approval_id`.
5. Checks that every inbound record's **producer** is declared in
   `external_text.producers` with a retention answer and a review date — RFC-0015.
   The unit is the producer, not the field: one entry per system is satisfiable,
   while one entry per field would turn this build red every time somebody else
   changed their schema, and would have us assert what a field of theirs holds.
   Also asserts the half of the rule we can enforce — **no value from an inbound
   record's `metadata` appears in a field this repository writes**. It passes
   trivially today because nothing copies; that is why it was worth adding before
   a convenience does. An expired `review_by` fails — what lapses is our obligation
   to re-read the entry, not the other repository's to publish a declaration, and a
   date no check opens is a backstop nobody reads. Exercise it with
   `--today 2026-11-01`.
6. Asserts the eight `event/v1` guarantees that JSON Schema cannot express:
   append-only, no silent state change, subject always answerable, `job_id` never
   fabricated, unresolvable tenant rejected at intake, external source preserved,
   no reasoning traces in an audit record, tenant partitions not mixed.
7. Checks that every entry in `known_gaps` still has an issue and an unexpired date.

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

## What a store owes — `store_contract.py`

```bash
python3 conformance/store_contract.py
python3 conformance/store_contract.py --implementation pkg.module:StoreClass
python3 conformance/store_contract.py --json
```

RFC-0014 step 3. The five obligations, run against an event store implementation
through the contract in `devfactory_observability/contract.py` — never by reaching
inside one. The in-memory `EventLog` is what it runs against today, and that is the
point: it is a **reference** implementation rather than a special case, so a durable
one added later runs the same suite with `--implementation` and fails here instead
of in production.

`OBLIGATIONS` is the written contract and the suite pairs every entry with a check
by number, refusing to run if one has no check. A sixth obligation added to the
declaration and nowhere else turns the run red rather than passing over five.

**It found something on its first run.** The reference implementation kept one set
of seen `event_id`s shared by every tenant, which made `append` answer the question
`read` refuses to answer: write a guessed id into an empty tenant, and whether it is
refused tells you another tenant holds it. Scoping the set per tenant changed no
test that existed — `event_id` is unique by construction, so nothing had ever
exercised the case. See the correction in RFC-0014 Decision 2.

Why this is not in `payload_check.py`: that file validates **payloads** against
pinned upstream contracts, and these obligations are about the **store**, owned
here, with no schema upstream to validate against. Mixing them would make one red
run mean two unrelated things.

## Is the pin still current — `pin_freshness.py`

```bash
python3 conformance/pin_freshness.py
python3 conformance/pin_freshness.py --today 2026-12-01   # to exercise the thresholds
```

Runs on a schedule rather than on push or pull request, because the event worth
noticing is **upstream moving while nothing here changes** — which no trigger tied
to this repository can see. It is the same reason `agent-platform`'s drift check
has a cron.

The gap this closes was measured, not imagined. The pin sat on one commit for 29
days while `agent-platform` moved 34 commits ahead, and three checks were running
the whole time without being able to say so: their drift check asks whether
`semantics_version` agrees, and it did; this directory's `payload_check` asks
whether payloads match the pinned schemas, and they did; our push and pull-request
workflows fire on changes here, and the change was elsewhere.

**Being behind is not a fault.** A pin that stays put because upstream has not
moved is a correct pin, and the check says `ok` for it at any age. What it reports
is *behind and left there*, which means real payloads are being validated against
an old copy of the contract.

Thresholds live in `pinned.yaml` under `freshness`, not in the script — declared
where a reader of the manifest can see them, for the same reason RFC-0013 requires
a declaration to be the artefact its checker reads. `fail_after_days: 60` is not an
invented number: ADR-0006 treats a `last_verified` older than 90 days as `unknown`
regardless of what the file says, so 60 leaves time to act before our own
`conforming` status lapses on its own.

**A `WARN` is only seen by someone who looks.** GitHub notifies on a red run, not
on a green one with an annotation, so the threshold that actually reaches a person
is the failure at 60 days. Read the warning as a note in the log, not as evidence
anyone was told.

An unreachable GitHub is not a verdict — the check reports it and exits `0`. A
check that goes red when the network is down teaches people to ignore it.

## Upgrading the pin

Always a separate PR: change `commit` in `pinned.yaml`, run the check, fix whatever
broke, then merge. Never mixed with feature work — otherwise there is no way to tell
whether a failure came from the contract or from the code.
