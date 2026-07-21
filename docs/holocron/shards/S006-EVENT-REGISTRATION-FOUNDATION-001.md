# S006-EVENT-REGISTRATION-FOUNDATION-001

## Status

```text
Type: HOLOCRON Shard Record
Purpose: Slice 006 implementation and verification evidence linkage
Status: Finalised pre-push execution record
Runtime effect: None
```

## Identity

```text
Shard: S006-EVENT-REGISTRATION-FOUNDATION-001
Repository: alchemyofacceptance/INTEVIA
Branch: main
Implementation commit: PENDING
Push SHA: PENDING
Message: feat(events): add governed event registration foundation
Migration: core.0010_event_registration_foundation
```

The implementation commit and push SHA remain placeholders. The implementation commit is assigned by the commit containing this record; push is not authorised at Shard finalisation.

## Purpose

Slice 006 establishes Event-owned, person-specific registration as a governed outcome with immutable receipts, transition lineage, evidence, idempotency, and database-enforced concurrency boundaries.

This Shard records implementation and verification evidence. It does not create runtime authority, grant publication authority, replace the future post-S006 Datacron, or convert repository regression evidence into S006 domain behavior.

## Accepted Human Decisions

The implementation preserves these accepted decisions:

- Event owns registration identity and lifecycle.
- A registration records a person-specific outcome, not attendance or participation proof.
- External authority remains the decision boundary; Event registration does not create an authority system.
- The authority target carries Event, participant, predecessor, origin, and whether acknowledgement is required.
- Registration records a bounded eligibility receipt rather than claiming complete policy reconstruction.
- Re-registration creates a new immutable registration linked to one cancelled predecessor.
- Cancellation records structured basis and provenance.
- Evidence attaches to the transition that produced it, not directly to the registration.
- Application writes to legacy `EventParticipation` are retired while historical reads remain available.

## Final Models And Services

The S006 implementation establishes:

- `EventRegistration` as immutable registration identity and bounded eligibility receipt;
- `EventRegistrationTransition` as append-only lifecycle, authority-context, idempotency, cancellation, and transition lineage;
- `EventRegistrationEvidenceReference` as immutable transition-only evidence;
- `EventRegistrationAuthorityTarget` as the explicit external-authority context;
- `EventRegistrationService` as the transactional registration, cancellation, replay, and operational-query surface;
- `EventParticipationWritesRetired` as the loud application boundary for legacy participation writes.

The registration lifecycle is deliberately minimal:

```text
new -> registered -> cancelled
cancelled predecessor -> new registered successor
```

## Authority-Context Boundary

Authority is evaluated externally through the existing `ContributionAuthority` capability contract. S006 supplies an immutable context containing:

- the locked Event;
- the participant who is the subject of registration;
- the predecessor, where re-registration applies;
- truthful self or third-party origin;
- the acknowledgement-required flag.

The resulting transition preserves the authority reference, evaluated Event, participant, predecessor, actor, and evaluation timestamp. This records the context used for the decision without granting Event registration independent authority.

## Acknowledgement Option C

Option C is preserved as a loud capability boundary:

- third-party origin is recorded truthfully;
- third-party registration may proceed only where prior acknowledgement is not required by the governing context;
- when prior participant acknowledgement is required but no acknowledgement capability exists, registration raises `AcknowledgementCapabilityUnavailable`;
- that failure leaves no registration, transition, or evidence residue.

S006 does not fabricate, infer, or silently bypass acknowledgement.

## Eligibility Receipt

Each registration records:

- Event state at registration;
- optional eligibility-policy reference;
- governed eligibility-basis type;
- eligibility-basis reference;
- eligibility evaluation timestamp;
- registration timestamp.

The receipt records what was evaluated at registration time. It does not claim that the complete governing policy can be reconstructed from the receipt alone.

## Idempotency

Registration and cancellation accept optional command idempotency keys. Database uniqueness is scoped by actor, action type, and non-null idempotency key.

A matching replay returns the already-persisted result. Reuse of the same key with a different Event, participant, or predecessor payload raises `IdempotencyConflict`. PostgreSQL race guardians prove that concurrent use of one key converges on one registration.

## Cancellation Basis And Provenance

Cancellation is represented by an append-only transition. It records a governed cancellation basis:

- participant request;
- actor decision;
- Event change;
- administrative;
- other.

It separately records whether the basis was participant-supplied or actor-recorded. Optional cancellation evidence attaches to that cancellation transition. Repeated cancellation is terminally idempotent and does not manufacture additional lineage.

## Transition-Only Evidence

`EventRegistrationEvidenceReference` belongs to exactly one registration transition. It has no direct registration foreign key. This preserves the action that produced the evidence and prevents evidence from floating free of lifecycle context.

Registration evidence is mandatory. Cancellation evidence is optional because the structured cancellation basis and source remain present even when no additional evidence reference is supplied.

## Predecessor Lineage

Re-registration requires a cancelled predecessor belonging to the same Event and participant. The predecessor:

- cannot reference itself;
- can have at most one successor;
- must be supplied explicitly when prior registrations exist;
- is repeated in the authority context preserved by the successor transition.

The prior registration remains immutable. Re-registration creates a new identity rather than reopening or rewriting the cancelled record.

## EventParticipation Write Retirement

`EventParticipation` remains available for historical reads and migration compatibility. New application writes are retired at both boundaries:

- direct model `save()` and `create()` reject new records;
- `EventService.attach_participant()` raises `EventParticipationWritesRetired` and directs callers to governed Event registration.

S006 does not delete or reinterpret historical participation rows.

## Migration 0010

```text
Path: core/migrations/0010_event_registration_foundation.py
Migration: core.0010_event_registration_foundation
Dependency: core.0009_care_response_foundation
SHA-256: DADE5FF169AF0A3B2BDEDFEB85270D9BFC9A60F4CBCFAE7CC2AA0C084801B6D8
```

The migration creates the three S006 tables and seven named catalogue objects:

```text
event_reg_lookup_idx
event_reg_transition_idx
one_active_event_registration
event_registration_not_self_predecessor
one_event_registration_successor
unique_event_reg_idempotency
unique_event_registration_evidence
```

Verified PostgreSQL predicates and boundaries include:

- one active registration per Event and participant where state is `registered`;
- no self-referential predecessor;
- one successor where predecessor is non-null;
- one idempotent command per actor, action type, and non-null key;
- unique evidence reference per transition.

## SQLite Evidence

SQLite verification established:

```text
S006 focused suite: 20 discovered; 13 passed; 7 PostgreSQL-only skips
Final repository suite: 155 discovered; 136 passed; 19 PostgreSQL-only skips
Django system check: no issues
Migration drift: no changes detected
```

A retained local SQLite database was protected through integrity checking and a byte-identical backup before environment verification. An accidental SQLite migration caused by non-persistent terminal environment selection was identified, governed, retained under Human authority, and followed by explicit in-process backend selector checks for PostgreSQL work.

## PostgreSQL Catalogue And Migration Evidence

Verification used the official `postgres:17` image at digest:

```text
sha256:a426e44bac0b759c95894d68e1a0ac03ecc20b619f498a91aae373bf06d8508d
```

The substrate was localhost-only, tmpfs-backed, without a named volume, and used execution-only credentials. Every consequential invocation proved the PostgreSQL vendor, loopback host, and expected database name in the same process.

Migration evidence confirmed:

- apply through `core.0010` succeeded;
- reverse to `core.0009` removed all three S006 tables and seven catalogue objects;
- reapply restored the tables, indexes, constraints, and predicates;
- Django system check passed;
- no migration drift was detected.

## Concurrency And Savepoint Evidence

PostgreSQL-specific guardians established:

- concurrent registration produces one winner and one governed duplicate outcome;
- concurrent registration with the same idempotency key replays one persisted registration;
- registration waiting on Event completion observes the committed terminal Event state and is refused;
- cancellation racing re-registration serialises and yields one cancelled predecessor with one active successor;
- a constraint failure inside a savepoint leaves the outer transaction usable;
- named constraints, indexes, and predicates exist in the PostgreSQL catalogue;
- migration reverse and reapply preserve the expected schema boundary.

Focused S006 PostgreSQL verification passed `20/20` with zero skips, failures, or errors.

## PostgreSQL Compatibility Repairs Exposed By Verification

Repository-wide PostgreSQL verification exposed pre-existing nullable outer-join locking incompatibilities in earlier domains. These are not S006 behavior.

Separate bounded repairs changed only aggregate lock retrieval and added PostgreSQL guardians:

- S001 Contribution locks the mutable `Contribution` aggregate without asking PostgreSQL to lock nullable joined context;
- S003 Library locks the mutable `LibraryResource` aggregate without asking PostgreSQL to lock nullable joined context;
- S004 Service locks the mutable `Service` aggregate without asking PostgreSQL to lock nullable joined context.

Authority, lifecycle, evidence, lineage, associations, transaction boundaries, and public method signatures remain unchanged. These repairs belong to the separate compatibility commit:

```text
fix(core): make aggregate locking PostgreSQL-compatible
```

## Final Verification Evidence

Evidence layers remain distinct:

```text
S006 implementation verification
PostgreSQL: 20/20 passed
SQLite: 13 passed; 7 PostgreSQL-only skips

Earlier-domain compatibility verification
Contribution and Library focused PostgreSQL: 34/34 passed
Service focused PostgreSQL: 17/17 passed

Repository-wide regression verification
PostgreSQL: 155/155 passed; zero skips
SQLite: 155 discovered; 136 passed; 19 PostgreSQL-only skips
Django system check: no issues
Migration drift: no changes detected
```

The repository-wide results prove integration across the tested repository. They do not convert all 155 tests into S006 domain tests.

## Known Deferrals

S006 deliberately defers:

- a participant acknowledgement workflow or acknowledgement persistence capability;
- reconstruction of complete governing policy from eligibility receipts;
- attendance or participation-proof replacement beyond registration;
- deletion or migration of historical `EventParticipation` rows;
- advanced Event lineage engines, semantic Event graphs, Event-driven automation, full notifications, full Locations behavior, payments, and marketplace behavior;
- production deployment and CI build-out;
- push, publication, and the post-S006 Datacron until separately authorised.

Known earlier S001 direct-update risks for `ContributionVersion` and `EvidenceReference` remain outside the PostgreSQL locking repair and outside S006.

## Commit And Push Placeholders

```text
Implementation commit SHA: PENDING
Push SHA: PENDING — IP-S006-PUSH-001 authority not granted
```

The post-S006 Datacron remains responsible for final commit and push linkage after authorised remote verification.

## Boundary Note

This Shard records bounded implementation and evidence. It does not grant authority, establish universal productivity claims, publish the Human reflection reserved for the Datacron and IDOP consideration, contact production systems, or authorise a push.
