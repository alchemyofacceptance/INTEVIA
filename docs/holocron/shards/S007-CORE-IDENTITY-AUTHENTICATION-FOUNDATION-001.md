# S007-CORE-IDENTITY-AUTHENTICATION-FOUNDATION-001

## Status

```
Type: HOLOCRON Shard Record
Purpose: Slice 007 implementation, verification, and evidence linkage
Status: Prepared post-push public lineage record
Runtime effect: None
```

## Identity

```
Shard: S007-CORE-IDENTITY-AUTHENTICATION-FOUNDATION-001
Repository: alchemyofacceptance/INTEVIA
Branch: main
Implementation and remote closure commit: 6aeca8986f28639c909b747e66dc78ac9c78b37c
Parent: 8635f0c3551bdec7c1f1f83affa4ce142b5c8152
Message: feat(core): add governed identity and authentication foundation
Migrations:
  core.0011_s007_identity_rename
  core.0012_s007_identity_foundation
  core.0013_s007_identity_constraints
Production-behaviour reference: PostgreSQL 17
Local inner-loop substrate: SQLite
```

## Purpose
Slice 007 establishes one durable Human Identity for INTEVIA without confusing the Human with a credential, membership, role, technical-administration status, session, or consequential authority.

The Slice transforms the existing persistent `Profile` model into canonical `Identity` in place, preserving historical attribution and primary-key continuity while adding governed identity lifecycle, authentication access, session invalidation, credential protection, and originating-Living-Organisation provisioning evidence.

This Shard records bounded implementation and verification evidence. It does not create Human authority, implement organisational membership, qualify production deployment, or grant publication authority beyond this lineage record.

## Accepted Human Decisions
The implementation preserves these accepted decisions:

- Transform persistent `Profile` into canonical `Identity` in place.
- Preserve primary keys and valid historical attribution relationships.
- Keep Django `User` as credential and session substrate rather than Human identity.
- Require originating-INTEVIA-Living-Organisation fulfilment evidence before activation.
- Keep identity, credential, membership, role, access, and consequential authority distinct.
- Retain `display_name` on `Identity`.
- Prohibit fallback from `display_name` to username or email.
- Preserve `ProfileRole` only as frozen compatibility debt for existing services.
- Retain Gee and Coe as unassigned historical vocabulary rather than Human identity or authority roles.
- Treat authentication as access establishment only.
- Require consequential services to re-evaluate current lifecycle and authority inside the authoritative transaction.

## Entering State
Before S007, S001–S006 used the persistent `Profile` model for Human attribution.

The repository did not yet provide:

- a canonical governed Human Identity;
- an explicit identity-access lifecycle;
- an immutable external identity identifier;
- protected credential replacement and retirement;
- session-epoch invalidation;
- an originating-Living-Organisation activation gate;
- a restricted-access shell;
- an authenticated read-only product surface;
- append-only identity lifecycle evidence.
S007 introduced these capabilities without reinterpreting historical Human attribution or implementing the deferred organisational-membership domain.

## Durable Human Identity
`Identity` is the durable Human-attribution object.

It includes:

- preserved historical primary-key identity;
- an immutable external UUID;
- a canonical username key;
- a protected credential relationship;
- a governed access lifecycle;
- an access epoch;
- lifecycle timestamps;
- append-only transition evidence;
- originating-membership provisioning intent and reconciliation evidence.
The Human is not reduced to the credential used to authenticate.

Changing or retiring a credential does not replace the Human Identity.

A session represents temporary authenticated access. It does not become the Human, membership standing, role, or authority.

## Canonical Username Boundary
Canonical usernames use a versioned normalization process including:

- Unicode NFKC normalization;
- case folding;
- trimming;
- explicit prohibited-character validation;
- durable uniqueness.
The canonical key supports consistent collision detection without claiming that all visually confusable characters are detected.

Confusable-character detection remains deferred.

## Access Lifecycle
The S007 access lifecycle is:

```
pending
  -> active
  -> restricted
  -> active
  -> deactivated

pending
  -> deactivated

restricted
  -> deactivated

deactivated
  -> active
```
The implementation supports governed:

- activation;
- restriction;
- deactivation;
- reactivation;
- username change;
- credential replacement;
- credential retirement.
Lifecycle changes produce append-only `IdentityTransition` evidence.

Deactivated identities cannot establish product access.

Restricted identities reach only the restricted shell and logout surface.

## Access Epoch And Session Invalidation
Each Identity carries an access epoch.

Authenticated sessions bind:

- Identity UUID;
- credential;
- access epoch;
- session start time.
Lifecycle or credential operations that invalidate existing access advance the epoch. A session holding an earlier epoch becomes stale and cannot continue as though the Identity state had not changed.

Sessions also enforce:

- browser-close expiry;
- an absolute eight-hour maximum.
Possession of an authenticated session does not establish consequential authority.

## Credential Protection
Django `User` remains the credential and session substrate.

S007 protects the credential relationship through governed atomic operations for:

- replacement;
- retirement.
The implementation prevents direct or unsafe credential reassignment from silently changing the Human Identity boundary.

Credential replacement races were challenged against PostgreSQL behaviour.

A replaced or retired credential does not erase the durable Identity or historical attribution.

## Originating-Living-Organisation Provisioning
Activation requires governed fulfilment evidence originating from the INTEVIA Living Organisation.

S007 records:

- provisioning intent;
- fulfilment and reconciliation evidence;
- duplicate and supersession boundaries;
- a governed first-Human provisioning command.
This evidence is consumed as an activation prerequisite.

S007 does not implement B4 organisational membership standing.

Provisioning evidence therefore does not itself create:

- membership;
- organisational role;
- constitutional designation;
- consequential authority.

## Authentication And Authority Boundary
Authentication establishes access only.

It does not establish consequential authority.

The following do not independently create Human Governor or product authority:

- authenticated session possession;
- staff status;
- superuser status;
- technical-operator status;
- `ProfileRole`;
- membership;
- credential ownership.
Consequential services must re-read current Identity lifecycle and authority inside the authoritative transaction before performing consequential work.

This prevents a prior login, cached state, staff flag, or technical-administration capability from silently becoming product authority.

## ProfileRole Compatibility Boundary
`ProfileRole` remains present only as a compatibility prerequisite for existing S001–S006 services.

It is not:

- governed membership;
- identity lifecycle authority;
- organisational authority;
- constitutional designation;
- Human Governor authority.
S007 does not deepen or reinterpret `ProfileRole`.

Its continued presence is explicit compatibility debt.

## EVENT Read Boundary
S007 adds an authenticated, read-only EVENT projection.

The projection is:

- relationship-bound;
- allowlisted;
- default-deny.
Restricted identities cannot use the normal authenticated EVENT read surface.

No Event mutation interface was introduced.

The projection does not convert authentication into Event authority or expose unrestricted domain data.

## Models And Migrations

### Migration 0011 — Identity Rename

```
Migration: core.0011_s007_identity_rename
```
This migration:

- renames `Profile` to `Identity` in place;
- renames credential and `ProfileRole` bindings;
- reconciles Django content types;
- reconciles Django permissions;
- preserves primary keys and historical attribution relationships.
The prior `core.profile` content type becomes exactly one `core.identity` content type.

Identity permissions become exactly:

```
add_identity
change_identity
delete_identity
view_identity
```

### Migration 0012 — Identity Foundation

```
Migration: core.0012_s007_identity_foundation
```
This migration adds:

- immutable external UUID;
- canonical username key;
- lifecycle fields;
- access epoch;
- lifecycle timestamps;
- `IdentityTransition`;
- provisioning requests;
- reconciliation evidence.

### Migration 0013 — Identity Constraints

```
Migration: core.0013_s007_identity_constraints
```
This migration applies durable database boundaries covering:

- identity uniqueness;
- lifecycle consistency;
- epoch consistency;
- credential protection;
- transition integrity;
- provisioning integrity.

## Historical Attribution Preservation
Representative populated S001–S006 data was migrated without attribution loss.

Verification preserved:

- existing Identity primary keys;
- existing credential bindings;
- existing `ProfileRole` links;
- all 27 historical attribution foreign keys.
The migration changes the canonical model identity without replacing or duplicating the historical Human-attribution record.

## Migration Reversibility Boundary
Before consequential S007 evidence existed, PostgreSQL migration qualification passed:

```
from zero
0010 -> 0013
0013 -> 0010
0010 -> 0013
```
Rollback and reapplication preserved the expected schema and attribution boundary.

Once consequential S007 evidence exists, forward repair is the expected recovery path. Migration reversibility evidence does not authorise destructive rollback of live consequential identity history.

## PostgreSQL Production-Behaviour Qualification
PostgreSQL 17 was exercised through a disposable localhost-only Docker substrate.

```
Image: postgres:17
Digest: sha256:a426e44bac0b759c95894d68e1a0ac03ecc20b619f498a91aae373bf06d8508d
Persistent volume: none
```
Qualified production-behaviour guardians covered:

- canonical username collisions;
- concurrent Identity creation;
- concurrent activation;
- provisioning duplication;
- provisioning supersession;
- credential replacement races;
- login versus deactivation;
- active consequential action versus deactivation;
- restriction versus EVENT read;
- stale sessions after reactivation;
- integrity-error rollback;
- transaction recovery.
SQLite remained the fast local test substrate. It was not treated as proof of PostgreSQL locking, constraint, race, or integrity-error behaviour.

## Qualification Repairs Earned
PostgreSQL challenge exposed and repaired:

- database access-state enforcement;
- the final locked consequential-authority recheck;
- locked EVENT Identity rereading;
- truthful refreshed provisioning-command output;
- invariant-based credential-replacement assertions;
- migration-test current-leaf restoration;
- migration-test isolation.
These repairs strengthened the implementation and its guardians without changing the accepted S007 architecture.

## Verification Evidence

```
Focused PostgreSQL:
61/61 passed
0 skipped
0 failed

Full PostgreSQL:
197/197 passed
0 skipped
0 failed

SQLite:
197 discovered
164 passed
33 expected PostgreSQL-only skips
0 failed
```
The PostgreSQL-only guardians skip explicitly under SQLite rather than presenting SQLite as evidence for backend-specific behaviour.

Repository-wide results establish integration across the tested repository. They do not convert all 197 tests into S007-specific tests.

## Code-Composition Evidence

```
Models and durable architecture: +409 / -55
Runtime and services:            +888 / -30
Migrations:                      +351 / -0
Authentication, session, UI:     +404 / -2
Tests and guardians:           +1,562 / -81
Other and configuration:          +23 / -1

Total bounded S007 change:
60 files
+3,637 / -169
```
These figures describe the bounded repository change. They do not independently establish product completeness, commercial value, or universal productivity claims.

## Independent Challenge
The S007 boundary passed through:

- SO-PRO organisational and architectural review;
- Claude repository-grounded Critical Challenge Review;
- exact local source-archive relay with SHA-256 verification;
- read-only live-database census before mutation;
- PostgreSQL migration, concurrency, lifecycle, session, authority, and integrity challenge.
Review names and stages are Human-ratified implementation lineage. Their technical consequences are visible through the committed architecture, migrations, services, constraints, guardians, and verification results.

## Human-Time Evidence
Clockify-attributable S007 adversarial review, implementation qualification, and closeout:

```
Total: 01:43:45

SO-PRO review:
00:18:29

CCR, implementation, and closeout:
01:25:16
```
The wider post-S006 programme-reconciliation work was recorded separately and is not attributed to the S007 Slice total.

## Known Deferrals
S007 deliberately defers:

- B4 membership implementation;
- `PrivacyProfile`;
- password reset;
- email authentication;
- email verification;
- OAuth;
- SSO;
- MFA;
- passkeys;
- confusable-character detection;
- public Human profiles;
- Event mutation UI;
- full dashboard;
- final visual design system;
- production deployment qualification;
- sustained-load PostgreSQL testing;
- distributed PostgreSQL testing.
These are bounded deferrals, not S007 implementation failures.

## Final Repository State At Slice Closure

```
HEAD: 6aeca8986f28639c909b747e66dc78ac9c78b37c
origin/main: 6aeca8986f28639c909b747e66dc78ac9c78b37c
Divergence: 0 / 0
Worktree: clean
Index: clean
```
No deployment, tag, release, or publication beyond the repository push had occurred at Slice closeout.

This public Shard is a later lineage record derived from the accepted Slice Record and remote commit. It does not rewrite the earlier implementation state or create runtime effect.

## Keeper

> S007 established the durable Human Identity through which INTEVIA preserves access, attribution, and lifecycle while keeping the Human distinct from credentials, membership, and consequential authority.

## Boundary Note
This Shard records bounded S007 implementation and evidence.

It does not:

- grant Human or product authority;
- convert authentication into consequential authority;
- implement B4 membership;
- activate deferred identity capabilities;
- qualify production deployment;
- claim that all repository tests are S007 behaviour;
- create a release or deployment;
- establish methodology, productivity, organisational, or commercial claims;
- require a new Datacron.
Final acceptance, repository mutation, commit, push, and public publication remain separate Human Governor authority moments.