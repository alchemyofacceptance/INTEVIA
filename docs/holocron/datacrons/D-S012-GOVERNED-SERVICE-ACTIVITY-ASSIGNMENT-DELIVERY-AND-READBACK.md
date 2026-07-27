# D-S012-GOVERNED-SERVICE-ACTIVITY-ASSIGNMENT-DELIVERY-AND-READBACK

## Status

```text
Type: HOLOCRON Datacron Record
Purpose: S012 lineage preservation
Phase: Post-implementation acceptance; post-Datacron MCP; pre-Datacron acceptance and pre-closure
Status: Repository lineage record pending Human Datacron acceptance
Runtime effect: None
```

This Datacron records the Human-accepted S012 development-phase
implementation, its governed discovery and implementation-packet lineage,
bounded execution, correction of compatibility guardians, final qualification,
and post-implementation Adversator review.

Committing this exact record does not constitute Human Datacron acceptance or
close S012. It does not activate an operational authority provider, run a
migration, deploy or publish INTEVIA, establish production readiness, or
promote a new IDOP version.

## Identity

```text
Datacron: D-S012-GOVERNED-SERVICE-ACTIVITY-ASSIGNMENT-DELIVERY-AND-READBACK
Slice: S012 - Governed Service Activity Assignment, Delivery and Readback
Domain owner: SERVICE
Human Governor: Carmian Owen
Repository: https://github.com/alchemyofacceptance/INTEVIA.git
Branch: main
Accepted implementation commit: d6ba2044a53766c863892681d39091200a4cc067
Implementation parent: 453c99c3279dd7a7324183a91e199880d9d0de7a
Commit subject: feat(service): add governed service activity orchestration
Environment: internal-pre-alpha
Controlling practice: IDOP machine-readable family v0.9.2
```

At post-implementation review time, `main` exposed the exact accepted commit
with no post-commit movement. The implementation diff contained exactly
thirteen Human-authorised paths, with 7,916 insertions and 6 deletions.

## Capability And Constitutional Meaning

S012 establishes a SERVICE-owned, application-layer orchestration foundation
for governed work:

- an authorised Human identity can create a Service Activity against one exact
  `ServiceVersion`;
- the Activity preserves its initiating domain and opaque external reference
  without importing the initiating domain's meaning;
- one Human may be assigned and may accept or decline the assignment;
- the assignee may record one reference-only Work Submission;
- an authorised reviewer may record one review;
- an authorised actor may complete the reviewed Activity;
- an authorised actor may cancel an eligible non-terminal Activity;
- an authorised reader may retrieve a neutral, lineage-bearing readback.

The capability records orchestration facts. It does not establish that work was
competent, valuable, accepted, approved, timely, productive, successful, or
beneficial. Completion records lifecycle occurrence only.

S012 advances the INTEVIA vision by allowing SERVICE to coordinate Human work
without annexing the initiating domain, converting workflow into performance
judgement, or allowing implementation access to manufacture authority.

## Evidence Classification

### Human Decisions

Carmian Owen accepted:

- the S012 discovery direction and D1-D14;
- H1-H9;
- HD-1-HD-3;
- X-1/H8 as a standing caller-validation boundary;
- Imp's A1-A10 amendments;
- Claude's precision annotations and OBS-1;
- T1 occurrence-time binding;
- T2 fresh authority gating with original-result replay;
- the candidate-v5 identity and validation record;
- the exact staged MCP authority for MUTATE, COMMIT, and PUSH;
- the thirteenth-path compatibility-guardian expansion; and
- the exact development-phase implementation at `d6ba2044…`, with Claude's
  post-implementation `PASS WITH CONDITIONS` boundaries preserved.

These were distinct Human decisions. None implies Datacron acceptance, S012
closure, deployment, publication, IDOP promotion, or operational activation.

### Repository-Verified Facts

Receiver-direct repository inspection established:

- accepted parent `453c99c3…`;
- one exact implementation commit `d6ba2044…`;
- exact commit subject;
- exact thirteen-path changed-file census;
- six SERVICE-owned persistence models;
- migration `core.0016_s012_service_activity_orchestration`;
- eight governed command methods;
- SERVICE-owned authority interfaces;
- closed readback DTOs;
- named constraints, indexes, canonical domains, lineage references, and
  replay controls;
- a corrected S011-B migration guardian that preserves `0015` lineage without
  requiring it to remain the repository-wide leaf; and
- clean local/remote equality after the one normal non-force push.

These facts establish repository identity and committed implementation. They do
not establish deployment, production operation, or current external authority.

### Producer-Executed Evidence

Imp's exact sealed execution return records:

```text
Focused SQLite: 157 discovered; 132 passed; 25 PostgreSQL-only skips
Focused PostgreSQL: 38 passed
Strengthened S012 PostgreSQL module: 25/25 passed
Final fully provisioned PostgreSQL plus Node regression: 544/544 passed
Migration drift: none
Django checks and SQL rendering: clean
Diagnostics across all thirteen paths: no errors
Exact changed/staged/committed path boundary: passed
Disposable PostgreSQL containers remaining: zero
Final repository and remote state: clean and equal
```

The final suite reported:

```text
Ran 544 tests in 423.877s - OK
```

This evidence was executed by the implementation producer under staged Human
authority. It remains `PRODUCER_ATTESTED` for other nodes unless they
independently reproduce it.

### Adversator Direct Inspection

Claude's post-implementation Adversator review directly inspected the accepted
commit and found:

```text
Determination: PASS WITH CONDITIONS
Repository correction required: none
Human product decisions remaining: none
Accepted requirements missing: none found
Unauthorised product decisions or unrelated mutation: none found
```

The Adversator directly inspected the core model block, migration,
`service_authority.py`, decisive command-service regions, read service, the
S007 Identity-FK guardian, and the corrected S011-B migration guardian. Four
S012 test modules were inspected by structure and naming rather than
assertion-by-assertion. No migration or test was executed by the Adversator
because that review was explicitly repository-read-only.

### Evidence-Class Boundary

AI-node agreement is not independent corroboration where one node consumes,
inherits, or reviews its own earlier recommendation. The record therefore
preserves:

- Imp's runtime results as producer-executed evidence;
- Claude's inspected repository facts as direct receiver inspection;
- Claude's unexecuted runtime statements as producer-attested rather than
  reproduced;
- self-echo disclosures for recommendations descending from Claude's own
  earlier review; and
- Human acceptance as a separate authority event, not an evidence-class
  upgrade.

## Six-Model Aggregate

Migration `core.0016_s012_service_activity_orchestration` introduced:

- `ServiceActivity`, the stable orchestration identity and current state;
- `ServiceActivityTransition`, the append-only command, authority, replay, and
  lineage occurrence;
- `ServiceActivityAssignment`, the one assignment occurrence;
- `ServiceWorkSubmission`, the one reference-only delivery occurrence;
- `ServiceActivityReview`, the one review occurrence; and
- `ServiceActivityEvidenceReference`, typed opaque evidence references bound to
  the transition that consumed them.

The aggregate protects exact `ServiceVersion` parentage. `ServiceProject`
remains deferred because it would add an empty aggregate for this Slice.

S012 stores opaque references, not submitted work content. It does not fetch,
copy, interpret, rank, score, or validate the external objects those references
identify.

## Lifecycle

The accepted lifecycle is:

```text
CREATE:
  <none> -> UNASSIGNED

ASSIGN:
  UNASSIGNED -> ASSIGNED

ACCEPT_ASSIGNMENT:
  ASSIGNED -> IN_PROGRESS

DECLINE_ASSIGNMENT:
  ASSIGNED -> DECLINED

SUBMIT_WORK:
  IN_PROGRESS -> SUBMITTED

REVIEW_WORK:
  SUBMITTED -> REVIEWED

COMPLETE_ACTIVITY:
  REVIEWED -> COMPLETED

CANCEL_ACTIVITY:
  UNASSIGNED | ASSIGNED | IN_PROGRESS | SUBMITTED | REVIEWED -> CANCELLED
```

`COMPLETED`, `DECLINED`, and `CANCELLED` are terminal. S012 contains no
`BLOCKED`, `HELD`, HOLD-release, reopen, reassign, withdraw, resubmit, or
uncomplete path.

Lifecycle legality is enforced both in the service and through the database
check constraint `s012_transition_edge_valid_ck`. This prevents a malformed
edge from becoming valid merely because a writer bypasses the ordinary command
service.

## Authority Boundary

S012 does not use or modify S004 `ContributionAuthority`. Its governed command
boundary is SERVICE-owned because the caller must receive and validate the
exact action, target, actor, access epoch, database alias, request reference,
idempotency key, evaluation time, authority reference, and provider result.

The standing X-1/H8 rule is:

> A governed authority boundary must return every field its caller is required
> to validate.

The authority request, response, and qualified decision are frozen structured
objects. Response echoes are compared field-by-field and fail closed. Technical
access, authorship, ownership, `ProfileRole`, staff status, superuser status,
authentication, or a historical receipt does not create S012 authority.

Only the current command actor and any proposed new assignee must satisfy
current active-account eligibility. Historical creators, assignees, reviewers,
and evidence suppliers remain lockable and identity-bound for lineage but need
not remain active. Deactivating a historical subject therefore does not freeze
otherwise lawful continuation or closure.

No operational authority provider is activated by S012. The capability defines
the contract and fail-closed seam for a future authorised provider.

## Locking, Concurrency, And Identity Epoch

The global locking design acquires the actor Identity together with its
credential using joined `select_for_update().select_related("credential")`,
without `of=`, then locks remaining distinct Identity rows in ascending primary
key order.

This aligns the SERVICE orchestration path with the existing Identity lifecycle
lock order rather than creating an inverse cross-service sequence.

Command execution:

- requires an active transaction on the declared database alias;
- refuses mismatched aliases;
- binds the qualified current `Identity.access_epoch` to the transition;
- uses fresh server-controlled `evaluated_at` for the current authority gate;
- binds every first-execution occurrence to the one normalised command
  `occurred_at`; and
- uses named-constraint, savepoint-scoped PostgreSQL winner recovery for
  authorised absent-row races.

SQLite does not claim the PostgreSQL concurrent-winner guarantee.

## Canonical Identity, Evidence, And Replay

S012 uses domain-separated canonical payloads for:

- command payload fingerprints;
- authority target fingerprints;
- authority decision references; and
- transition lineage references.

Canonical time is UTC with exact microsecond precision. Floats, naive
datetimes, malformed digests, missing fields, inconsistent echoes, wrong
database aliases, wrong actors or epochs, and lineage mismatches fail closed.

The idempotency boundary is the exact actor, action, and idempotency key.
Replay first qualifies fresh current authority as a gate, then validates the
stored original transition, payload, target, result, authority decision, and
lineage.

An exact replay returns the unchanged original result and original authority
references even when later state or parent state has changed. Fresh authority
does not rewrite historical evidence. A payload, timestamp, actor, epoch,
target, or key mismatch is malformed lineage and fails closed.

## Readback And Human-Facing Truth

The read service locks the Activity first and reconstructs its lineage before
projection. The creator and current assignee have structural visibility.
Other readers require the explicit visibility provider; anything other than
exact positive visibility is denied.

Readback recomputes payload fingerprints and authority-decision references and
fails closed on malformed or inconsistent lineage.

Human-facing transfer uses frozen, closed-allowlist DTOs. No field, wording,
derivation, or disclosure is added by implication. The DTOs exclude live model
objects, unrestricted navigation surfaces, unrelated domain data, internal
performance inference, and mutable database state.

Neutral presentation records the Activity and its raw chronological lineage.
It does not derive or expose duration, productivity, responsiveness, ranking,
urgency, quality, value, or performance signals.

## Exact Implementation Boundary

The accepted implementation changed exactly:

```text
core/models.py
core/migrations/0016_s012_service_activity_orchestration.py
src/intevia/services/service_authority.py
src/intevia/services/service_activity_service.py
src/intevia/services/service_activity_read_service.py
tests/test_service_authority.py
tests/test_service_activity_models.py
tests/test_service_activity_service.py
tests/test_service_activity_readback.py
tests/test_service_activity_postgresql.py
tests/test_service_activity_migrations.py
tests/test_s007_postgresql.py
tests/test_event_resource_relationship_migrations.py
```

The first twelve were the accepted candidate-v5 implementation boundary. The
thirteenth was separately authorised after the full regression exposed an
S011-B guardian that incorrectly required migration `0015` to remain the
repository-wide leaf.

The correction preserved the real S011-B invariant:

- `0015` exists;
- `0015` depends on `0014`;
- `0015` contains no `RunPython`;
- forward, reverse, and reapply remain valid; and
- current repository leaf discovery is dynamic.

It does not hard-code or annex S012's `0016` migration into the S011-B claim.

## Truthful HOLD And Correction Lineage

The execution lineage preserves rather than erases its HOLDs:

1. an initial execution route could not qualify the exact repository path;
2. a corrected route named the wrong Windows path and held again;
3. the exact receiver-qualified repository path was established;
4. the full regression exposed one stale compatibility guardian outside the
   then-authorised twelve-path boundary;
5. Imp reported a `GOVERNANCE QUESTION` without waiving the failure or mutating
   out of scope;
6. Carmian Owen granted exact path-level authority for the one compatibility
   guardian;
7. the guardian was corrected, all relevant tests were rerun, and the final
   suite passed; and
8. exactly one commit and one normal non-force push followed the completed
   gates.

Each predecessor return remains truthful at issue. Later success does not turn
an earlier unavailable route into a route that had been available, or an
earlier path boundary into authority it did not contain.

## IDOP And HAT Significance

S012 demonstrates the refined IDOP sequence in a demanding implementation:

```text
discovery
  -> architecture and packet challenge
  -> independent narrow escalation
  -> Human decisions
  -> implementation-feasibility review
  -> Adversator review
  -> corrected successor packet
  -> exact MCP preparation
  -> staged MUTATE / COMMIT / PUSH
  -> post-implementation semantic inspection
  -> Human implementation acceptance
  -> candidate Datacron
```

The process surfaced material decisions before code, found thin return types
before they hardened into interfaces, distinguished green tests from semantic
inspection, corrected guardians rather than waiving them, and preserved Human
authority over meaning.

The work also exercised frontier multi-node file exchange. A one-file carrier
plus a direct Human paste prompt proved to be a workable governed fallback when
native cross-node transfer was unavailable. Tool-generated `base64Content`
from sealed staged bytes, followed by metadata and raw-byte readback, preserved
exact file identity without model transcription. These are observed-practice
inputs for IDOP v0.9.3; this Datacron does not promote them.

AI nodes supplied discovery, architecture, implementation, challenge,
qualification, custody, and review. Carmian Owen retained terminal authority
for scope, consequential design choices, mutation, Commit, Push, acceptance,
Datacron, and closure.

## Known Documentation Divergence

The accepted implementation packet records a deferred documentation divergence:
the Implementation Plan, Architecture Map, or systems-design descriptions may
use broader or earlier SERVICE wording than the exact S012 foundation now
implemented.

S012 does not silently rewrite those programme documents. Their later
reconciliation requires separate scope and authority. This Datacron records the
known divergence without declaring it resolved.

## Development-Phase Conditions

The post-implementation `PASS WITH CONDITIONS` carries these exact non-blocking
bounds:

1. Claude did not independently execute migrations, tests, or PostgreSQL
   concurrency. Those outcomes remain producer-attested by Imp.
2. Four S012 test modules were inspected by structure and naming rather than
   assertion-by-assertion during Claude's review.
3. The accepted claim is development-phase adequacy only.

These conditions did not require repository correction before Human
implementation acceptance. They remain explicit limits on later claims.

## Cost And Time Evidence

Clockify is the Human's authoritative time ledger. Conversation-visible
durations and node-credit observations are useful operational evidence, but no
complete, reconciled S012 time-and-cost record is bound into this candidate.
No audited total is therefore asserted here.

## Retained Boundaries And Deferrals

This Datacron does not:

- constitute Human Datacron acceptance merely because it exists in the repository;
- close S012;
- activate an operational authority or visibility provider;
- run or apply migration `0016` outside the qualified test environments;
- create operational Service Activities or personal-data-bearing records;
- deploy, publish, release, tag, or represent production readiness;
- establish security, privacy, compliance, performance, accessibility,
  longitudinal, or Human-usability readiness;
- infer competence, approval, value, productivity, responsiveness, urgency, or
  performance from Activity state or lineage;
- fetch or interpret referenced work content;
- create `ServiceProject`, `BLOCKED`, `HELD`, HOLD release, reopen, reassign,
  withdraw, resubmit, or uncomplete semantics;
- resolve the known documentation divergence;
- upgrade producer-attested runtime evidence to independent reproduction;
- turn a digest, receipt, authority reference, or prior successful command into
  continuing permission;
- revise or promote IDOP v0.9.2 or any v0.9.3 observation;
- authorise S013 or any other future Slice; or
- imply that development-phase acceptance is deployment or operational
  acceptance.

Historical evidence is lineage, not continuing permission. Every future
consequential action remains subject to current Human authority and its owning
domain.

## Repository And MCP Boundary

The exact repository destination used by the separately authorised Datacron
MCP is:

```text
docs/holocron/datacrons/D-S012-GOVERNED-SERVICE-ACTIVITY-ASSIGNMENT-DELIVERY-AND-READBACK.md
```

The authorised commit subject is:

```text
docs(datacron): record governed service activity orchestration
```

Repository creation remains governed by the separately accepted Datacron MCP.
Committing this exact object does not itself constitute Human Datacron
acceptance or S012 closure. The reviewed candidate and Imp review remain the
controlling predecessor evidence for this mechanically promoted record.

## Human Decision Boundary

The exact S012 development-phase implementation is Human-accepted. This
candidate prepares later separate decisions:

1. accept or decline the exact S012 Datacron after its governed review and
   separately authorised repository creation; and
2. close or retain open S012 after considering the committed Datacron and
   retained conditions.

Readiness evidence is not either decision. Datacron acceptance does not
silently close S012. S012 closure does not deploy, publish, promote IDOP,
activate an operational provider, or authorise a future Slice.

Final authority remains with Carmian Owen, Human Governor.
