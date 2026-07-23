# D-S010-GOVERNED-DIRECT-SELF-REGISTRATION

## Status

```text
Type: HOLOCRON Datacron Record
Purpose: Lineage preservation
Phase: Post-Slice 010 implementation, maintenance, review, and Human qualification
Status: Settled lineage record
Runtime effect: None
```

## Identity

```text
Datacron: D-S010-GOVERNED-DIRECT-SELF-REGISTRATION
Title: Governed Direct Self-Registration
Slice: S010 — Governed Direct Self-Registration
Domain: EVENT
Human Governor: Carmien Owen
Date range: 23 July 2026
Repository: alchemyofacceptance/INTEVIA
Branch: main
Implementation parent: f95a528c971dbb9c894a9683b208a068163099bf
Implementation commit: 32d0ef05e44ce2d16025b8813e1784cadcb11232
Maintenance commit: fefec9c2387a5691cbd78290793249f87f3856a1
Final accepted S010 HEAD: fefec9c2387a5691cbd78290793249f87f3856a1
Policy: REG-AUTH-PREALPHA-001 v1
Policy path: governance/policies/REG-AUTH-PREALPHA-001-v1.md
Policy SHA-256: 81d360974bf31dd355c1f064a8accd8dd2a10e9dd25e6e8b6db0e8714a6f5051
Local inner-loop substrate: SQLite
Production-behaviour reference: PostgreSQL 17
```

This Datacron preserves S010 lineage and the bounded Human determination. It
does not create runtime identity, authority, policy, implementation state,
deployment state, publication state, methodology status, or a universal claim
about Human or AI-assisted work.

## Evidence Classification

### Repository Proof

Repository proof includes the two commits, policy instrument, runtime and
presentation paths, guardians, Git history, exact path inventories, final
local/remote equality, and clean worktree and index. It establishes what was
committed, not Human usability or hosted readiness.

### Executable Verification

Executable verification includes compile/import checks, SQLite and PostgreSQL
guardians, focused and full regressions, migration-drift and Django system
checks, secret-safe scanning, substrate qualification, and teardown. It proves
the behavior exercised by those checks; it does not turn every regression into
an S010 domain claim.

### External Challenge

`CCR-S010-POST-001` independently reviewed policy, authority, eligibility,
transactions, visibility, privacy, presentation, guardians, PostgreSQL method,
deployment boundaries, and successor inheritance. Its determination was
`PASS WITH CONDITIONS — READY AFTER NAMED NON-IMPLEMENTATION CONDITIONS`.
Review did not create mutation, qualification, closure, or deployment authority.

### Human Observation, Trace, And State

`IDOP-CER-S010-HQ-001` records Human-visible browser observations and the
Human determination. The localhost server trace aligned with login, Event GET,
registration POST, redirect, refresh, history GET, denial POSTs, and logout.
Disposable persisted state showed one owner registration and no staff or
superuser registration before verified teardown.

### Guardian-Supported, Not Directly Observed

The exact repeat phrase, `A current registration record already exists for
you.`, was not directly exercised in the live UI because successful creation
removed the control. Duplicate safety is guardian-supported. Human-visible
evidence directly established one durable current record, one history entry,
and no duplicate through refresh or navigation. This record does not represent the unobserved phrase as Human-verified.

### Interpretation And Deferral

The Human determination and IDOP contributions are bounded interpretations.
Hosted readiness, general usability, accessibility, performance at scale,
external transferability, compliance, regulatory status, and universal
methodology remain unproven or deferred.

## Parent Lineage

```text
INTEVIA V1 Evolution
    -> S006: Governed Event Registration Foundation
    -> S007: CORE Identity and Authentication Foundation
    -> S008: Governed Event Attendance Foundation
    -> S009: Integrated Personal Home and Event Journey
    -> S010: Governed Direct Self-Registration
    -> D-S010-GOVERNED-DIRECT-SELF-REGISTRATION
```

S010 composes existing EVENT registration, CORE authentication, personal Event
visibility, and history readback into bounded authenticated self-submission.
It does not transfer EVENT meaning to CORE or merge registration with attendance.

## Intent And Scope

S010 permits an authenticated active Identity to attempt direct
self-registration for an already-visible Event owned by that Identity. The
Identity and Event must be explicitly allowlisted. The Event must be
`published` or `active`. Eligibility is intrinsic, EVENT-owned, and derived
from trusted Event configuration rather than Human input.

```text
Action: register_self
Environment: internal-pre-alpha
```

S010 does not implement third-party registration, cancellation,
re-registration, attendance mutation, approval workflow, policy discovery,
deployment, hosted enablement, or operational enablement.

## Authority Model

Human authority remained terminal throughout discovery, architecture,
implementation, maintenance, review, verification, qualification, recovery,
commit, push, and Datacron authority.

The Human-authored instrument is `REG-AUTH-PREALPHA-001 v1`, the Internal
Pre-Alpha Direct Self-Registration Authority Policy. It requires:

- action `register_self` and environment `internal-pre-alpha`;
- authenticated active credential and Identity;
- actor and subject to be the same Identity;
- explicit Identity and Event allowlists;
- Event ownership by the acting Identity;
- Event state `published` or `active`;
- explicit enablement, effective and expiry bounds;
- no revocation or supersession;
- fail-closed handling of absent, malformed, mismatched, expired, revoked, or
  superseded state;
- absolute staff and superuser refusal even when all other conditions qualify.

Authentication, visibility, ownership, role, staff or superuser status,
allowlist membership in isolation, references, receipts, and evidence do not
create authority. Runtime code implements but does not author, amend,
supersede, discover, generalise, or enlarge Human policy. Environment write
access is a policy-enablement authority surface. Deployment and enablement
remained separately withheld except for the authorised, torn-down process-local
qualification environment.

## Implementation

### Implementation Commit

```text
32d0ef05e44ce2d16025b8813e1784cadcb11232
feat(event): add governed direct self-registration journey
```

Exact changed paths:

```text
core/templates/core/base.html
core/templates/core/event_detail.html
core/views.py
intevia/urls.py
src/intevia/services/event_registration_policy.py
src/intevia/services/event_registration_service.py
src/intevia/services/event_self_registration_service.py
tests/test_s007_postgresql.py
tests/test_s010_direct_self_registration.py
tests/test_s010_direct_self_registration_postgresql.py
```

The commit introduced a POST-only authenticated route, direct journey service,
fail-closed policy implementation, trusted eligibility integration, frozen
outcomes, Event-page readback, and focused guardians. It reused the registration
aggregate, evidence and transitions, authority adapter, shared visibility
predicate, and history surface. Trusted eligibility fields cannot be supplied
by the Human request.

### Maintenance Commit

```text
fefec9c2387a5691cbd78290793249f87f3856a1
fix(event): enforce pre-alpha account denial
```

Exact changed paths:

```text
governance/policies/REG-AUTH-PREALPHA-001-v1.md
src/intevia/services/event_registration_policy.py
tests/test_s010_direct_self_registration.py
```

Maintenance committed the Human-authored instrument and placed staff and
superuser checks at the start of the account-refusal predicate. Refusal is
decisive before registration, transition, eligibility receipt, or evidence
persistence. Separate guardians prove account refusal and zero residue.

### Presentation And Readback

The frozen phrases include:

```text
Your registration record has been created.
A current registration record already exists for you.
Registration is not available through this surface for this Event.
This registration cannot be completed through this account.
We could not complete the registration right now. No registration was created.
We could not confirm the result. Return to the Event page to check the current registration record.
```

The Event page provides durable current-record readback and subject-safe
history. Registration and attendance remain visibly and structurally separate.
S010 introduced no model, schema, migration, settings, or database contract.

`Registration is not available through this surface for this Event.` is the
bounded unavailable outcome for non-qualifying action or Event conditions,
including owner, state, and Event-allowlist conditions. It does not reveal
which specific unavailable condition failed.

## Verification Evidence

```text
Compile/import: passed
S010 SQLite: 17/17 passed
Focused S006/S009/S010 SQLite: 51/51 passed; 11 expected backend-only skips
Full SQLite: 231/231 passed; 52 expected backend-only skips
S010 PostgreSQL: 21/21 passed; zero skips
PostgreSQL-specific modules: 52/52 passed; zero skips
Full partitioned PostgreSQL: 283/283 passed across 48 modules
Unresolved PostgreSQL warnings: zero
Migration drift: none
Django system checks: no issues
Schema, migration, settings, route, and template maintenance delta: zero
Introduced suspected-secret matches: zero
Disposable PostgreSQL containers and volumes after teardown: zero
Process database credentials after teardown: absent
Final maintenance HEAD == origin/main: fefec9c2387a5691cbd78290793249f87f3856a1
Final maintenance divergence: 0/0
Final maintenance worktree and index: clean
```

These results preserve proportional implementation and integration evidence.
They do not turn the full repository regressions into a claim that every test
describes S010 domain behavior.

PostgreSQL used official `postgres:17`, loopback-only dynamic binding, tmpfs,
generated process-local credentials, no named volume, and mandatory teardown.
One partitioned S010 invocation left one disposable test-database session.
Under resumed Human authority, the harness inspected structural metadata,
terminated only that disposable session, confirmed zero remaining sessions,
and completed 283/283. The condition did not recur and was not suppressed.

## HOLD And Recovery Lineage

### Controlling Source And Package Delivery

**Trigger:** an early CCR handoff had an unfilled hash and differing filename;
maintenance later required exact bundle, manifest, member, IDOP, decision, CCR,
Slice, baseline, and authority agreement.

**Classification:** source was not receiver-verifiable.

**Minimum Human decision/evidence:** authoritative named bundle and hash, then a
final packet carrying scoped execution authority.

**Preserved work:** unverified material remained non-controlling; substantive
review and mutation did not advance from it.

**Resolution:** corrected single-bundle delivery was byte-verified; the final
packet superseded only pending authority state.

**IDOP refinement:** controlling-source qualification, package-and-prompt atomic
handoff, receiver-visible attachment verification, build-download-attach relay,
single-bundle delivery, and downloadable complete review return.

### PostgreSQL Substrate

**Trigger:** the prepared packet required cross-substrate closure but did not
itself authorise PostgreSQL substrate creation.

**Classification:** SQLite verification could proceed; commit could not.

**Minimum Human decision:** grant one disposable PostgreSQL substrate.

**Preserved work:** three-path maintenance and SQLite evidence remained intact.

**Resolution:** the final packet granted qualified local creation and teardown.

**IDOP refinement:** pre-qualified non-default substrate and teardown evidence.

### Adjacent Guardian Maintenance

**Trigger:** implementation updated the S007 PostgreSQL foreign-key catalogue
guardian for five accepted S008 attendance references.

**Classification:** bounded stale-guardian maintenance, not S010 behavior and
not weakening strict equality.

**Minimum Human decision:** exact adjacent guardian authority.

**Preserved work:** strict catalogue equality and S008 lineage.

**Resolution:** expectation moved from 27 to 32 exact references; later policy
maintenance did not reopen the guardian.

**IDOP refinement:** pre-qualified adjacent guardian maintenance.

### Disposable PostgreSQL Session Teardown

**Trigger:** `tests.test_event_attendance_postgresql` passed 15 tests, but
Django could not drop its disposable database while one session remained.

**Classification:** verification-stage HOLD; unresolved database errors blocked
commit.

**Minimum Human decision:** one fresh run and bounded disposable-session
inspection and termination without repository mutation.

**Preserved work:** all prior code and green SQLite/S010 PostgreSQL evidence;
no commit or push occurred during HOLD.

**Resolution:** a fresh per-module run terminated one disposable S010 session,
confirmed zero remaining, completed 283/283, and proved teardown. The condition
did not recur during the remaining partitioned regression and was not suppressed.

**IDOP refinement:** disposable test-session teardown as an explicit boundary.

### Human Fixture Topology

**Trigger:** one Event could not make three distinct credential Identities
otherwise qualifying under owner-only policy.

**Classification:** fixture topology contradicted policy predicates.

**Minimum Human decision:** three Events, each owned by its tested Identity.

**Preserved work:** no fixture, policy state, or repository change was created.

**Resolution:** exactly three active allowlisted Identities and three published
allowlisted Events, one corresponding owner per Event.

**IDOP refinement:** pre-qualify Human fixtures against every policy predicate.

### Human-Visible Input Surface

**Trigger:** an Agent-owned terminal reached a hidden password prompt but could
not be surfaced as an interactive Human-visible terminal.

**Classification:** credential input surface unavailable; chat or hidden input
would violate secret handling and Human control.

**Minimum Human action:** abandon Agent routing and run a self-cleaning launcher
manually in a Human-visible terminal.

**Preserved work:** no password entered chat; processes and disposable data were
removed; repository state stayed clean.

**Resolution:** the Human entered passwords only in the visible terminal,
completed qualification, and observed self-cleaning teardown.

**IDOP candidate refinement:** qualify Human-visible execution surfaces before
secret-bearing or judgement-bearing interaction; this is not universal method.

## External CCR And Human Decisions

The external CCR required no general implementation reopening. Its conditions
were adopted, adapted, deferred, or kept deployment-only as follows:

- the policy instrument was committed in the governance tree;
- staff/superuser ambiguity was resolved as absolute refusal and guarded;
- the recovery instruction was adopted: `Return to the Event page: if the
  record is shown, it was created; if not, it was not; resubmitting is safe
  either way.`;
- localhost was distinguished from hosted deployment;
- `ATOMIC_REQUESTS=False` and environment authority were qualified;
- deployment warnings remained hosted/deployment gates;
- v1 eligibility/authority overlap was not promoted into a cross-domain pattern;
- header equivalence, grouped-PG hygiene, and an expiry-boundary test remained
  informational or deferred;
- third-party registration, cancellation, re-registration, attendance,
  deployment, and S011 remained outside scope.

Human decisions adapted findings without converting review into authority.

The recovery instruction was qualification guidance, not an additional frozen
UI phrase. The frozen indeterminate UI phrase directs the Human back to the
Event page; guardian evidence supports safe resubmission. Human qualification
did not directly exercise the repeat-submission phrase.

## Human Qualification

Qualification used localhost Django, disposable SQLite data, a clean repository
at `fefec9c2387a5691cbd78290793249f87f3856a1`, `ATOMIC_REQUESTS=False`, and
process-local policy enablement. `False` was the required and verified
qualification state; `ATOMIC_REQUESTS=True` was not qualified by this exercise.
Exactly three active allowlisted Identities and three published allowlisted
Events were used. Each Identity owned its Event.

### Owner

The owner saw the owned Event and no starting registration. One submission
showed `Your registration record has been created.` The redirected page showed
`A current registration record exists for you.` Refresh and navigation
preserved state. History showed one creation entry and timestamp. Attendance
remained separate. No product-surface or server error occurred.

### Repeat-Submission Evidence Note

After creation, the UI removed the control. The exact repeat phrase was not
directly exercised. Human observation established durable readback, one current
record, one history entry, and no duplicate through refresh or navigation.
Guardians support duplicate safety. This is an evidence note, not a defect or
reopening requirement.

### Staff And Superuser

Each otherwise qualifying Identity used its own visible, published, allowlisted
Event. Each submission showed exactly `This registration cannot be completed
through this account.` Each Event page showed no registration record. No
misleading success state or product-surface error was observed.

### Recovery And Teardown

Live Event-page readback and history supported comprehension of the recovery
instruction without injected post-commit failure. Browser result, server trace,
and disposable state aligned. The Human stopped the launcher, observed
`QUALIFICATION_ENVIRONMENT_TORN_DOWN`, and the server, database, policy state,
and launcher were removed. The repository remained clean.

## Human Determination

# PASS WITH EVIDENCE NOTE

The owner journey, durable readback, truthful history, and absolute staff and
superuser denial passed visibly and agreed with server trace and persisted
state. The unobserved repeat phrase limits the Human-observation claim but does
not indicate a defect or reopening requirement.

## IDOP Evolution Exercised By S010

S010 carried forward and materially re-exercised controlling-source,
receiver-verification, handoff, conditional-authority, substrate, and adjacent
guardian practices already present in the living IDOP. It additionally exposed
bounded refinements around disposable test sessions and Human qualification.
The materially exercised or established practices were:

- controlling-source qualification and reusable file memory;
- package-and-prompt atomic handoff and receiver-visible verification;
- downloadable complete review return;
- pre-granted conditional stage authority;
- build-download-attach relay and single-bundle delivery;
- pre-qualified disposable verification substrate;
- pre-qualified adjacent guardian maintenance;
- executable Human handoff assembly;
- disposable test-session inspection and teardown;
- pre-qualified Human fixture topology;
- Human-visible execution-surface qualification as a candidate refinement.

These align with materially exercised current living IDOP Sections 19-31. IDOP
is observed practice, not independent authority. S010 claims no universal
methodology status or external transferability.

## Successor Inheritance

S011 and later work must inherit at minimum:

- owner-only authority remains bounded unless separately widened;
- staff and superuser refusal remains absolute under this policy;
- Event visibility does not imply action authority;
- registration and attendance remain separate;
- policy v1 eligibility/authority overlap is not a general template;
- Human fixture topology must be checked against policy predicates;
- non-default substrates and Human-visible input surfaces require qualification;
- the S007 PostgreSQL foreign-key catalogue guardian carries 32 accepted
  Identity references, including the five S008 attendance references;
- the repeat-submission phrase remains guardian-supported evidence, not a
  defect or directly Human-observed result;
- S010 closure implies no deployment or operational enablement.

S011 implementation was not authorised or performed.

## Explicit Non-Authority

This Datacron does not authorise or establish deployment, hosted or shared
enablement, production readiness, operational programme data, third-party
registration, cancellation, re-registration, attendance mutation, approval
workflow, amendment or expansion of `REG-AUTH-PREALPHA-001 v1`, use of its
owner-only allowlist pattern as a general cross-domain template, S011
implementation, publication, compliance or regulatory claims, accessibility
or scale qualification, generic policy infrastructure, or unnamed authority.

## Evidence Chain

```text
HOLOCRON
    -> Datacron: D-S010-GOVERNED-DIRECT-SELF-REGISTRATION
    -> Implementation parent: f95a528c971dbb9c894a9683b208a068163099bf
    -> Implementation: 32d0ef05e44ce2d16025b8813e1784cadcb11232
    -> Maintenance: fefec9c2387a5691cbd78290793249f87f3856a1
    -> Policy: REG-AUTH-PREALPHA-001 v1
    -> Policy SHA-256: 81d360974bf31dd355c1f064a8accd8dd2a10e9dd25e6e8b6db0e8714a6f5051
    -> S010 Shard: none required by the verified canonical Datacron pattern
    -> External review: CCR-S010-POST-001
    -> Human qualification: IDOP-CER-S010-HQ-001
    -> SQLite: 231/231 plus 52 expected backend-only skips
    -> PostgreSQL: 283/283 across 48 modules
    -> Human determination: PASS WITH EVIDENCE NOTE
    -> Accepted S010 HEAD: fefec9c2387a5691cbd78290793249f87f3856a1
```

## Closing Statement

S010 established bounded owner-only authenticated direct self-registration over
already-visible Events. EVENT retained registration and eligibility meaning.
Human authority remained terminal. Policy failed closed and absolutely refused
staff and superuser credentials under otherwise qualifying conditions. SQLite,
PostgreSQL, external challenge, and Human-visible qualification converged on
the same bounded result.

This Datacron preserves that result and evidence note without converting
localhost qualification into deployment, product-wide validation, policy
expansion, S011 authority, publication, compliance, or universal methodology.

## Post-Datacron State

```text
Slice: S010
Direct self-registration: Implemented and remotely established
Policy instrument: Committed and byte-qualified
Staff and superuser denial: Absolute under REG-AUTH-PREALPHA-001 v1
SQLite and PostgreSQL maintenance regressions: Green
Human qualification: PASS WITH EVIDENCE NOTE
Deployment and operational enablement: Not authorised
S011: Not authorised
Runtime effect of this record: None
```

## Boundary Note

This lineage record introduces no runtime behavior, model, schema, migration,
test, dependency, setting, route, template, policy mutation, deployment,
operational data, S011 implementation, publication, compliance claim, or
historical rewrite.
