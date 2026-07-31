# D-S014-GOVERNED-EDUCATION-COURSE-DEFINITION

## Status

```text
Type: HOLOCRON Datacron Record
Purpose: S014 lineage preservation
Phase: Post-implementation Commit/Push and closure analysis; post-Datacron MCP;
       pre-Human implementation acceptance, pre-Datacron acceptance, and pre-closure
Status: Repository lineage record pending direct Human acceptance and closure
Runtime effect: None
```

This Datacron records the closure-ready S014 development-phase implementation,
the exact governed education course foundation it establishes, its bounded
review and correction lineage, the abandoned expanded-QA experiment, the
state-aware recovery that restored the accepted closure basis, and the
Human-selected deferral of `MAT-S014-01` to IDOP v0.9.6.

Committing this exact record does not itself constitute Human implementation
acceptance, Human Datacron acceptance, or S014 closure. It does not deploy or
publish INTEVIA, activate an operational authority provider, create an
operational Course, promote IDOP, authorise S015, or erase any preserved
residual or failed experimental result.

## Identity

```text
Datacron: D-S014-GOVERNED-EDUCATION-COURSE-DEFINITION
Slice: S014 - Governed Education Course Definition
Domain owner: EDUCATION
Human Governor: Carmian Owen
Repository: https://github.com/alchemyofacceptance/INTEVIA.git
Branch: main
Implementation commit: 4530d593127b163c1bafb8d79f03349c61d9335a
Implementation parent: c3832b48edae798f637cc7570913d466fc9e6e63
Implementation tree: 8562e29fdc3d150b38cc5eb14eb3a194858bff96
Commit subject: S014: add governed education course foundation
Environment: internal-pre-alpha
Controlling protocol: IDOP v0.9.5
Predecessor protocol: IDOP v0.9.4 preserved as superseded lineage
```

The implementation commit is exactly one commit beyond the accepted and
closed S013 baseline. It changes exactly the sixteen Human-authorised S014
paths. S013 remains closed and was not reopened.

## Capability And Constitutional Meaning

S014 establishes the smallest executable EDUCATION foundation in which one
explicitly authorised active Human identity can:

- create a stable draft Course definition with immutable version 1;
- append an exactly ordered immutable successor CourseVersion; and
- when that Identity is the Course creator, retrieve validated current,
  exact-version, and complete-lineage projections.

The stable `Course` is the aggregate identity and current-version pointer.
Each `CourseVersion` is an immutable definition occurrence preserving its
actor, authority evidence, request and idempotency lineage, definition basis,
canonical payload, predecessor, and lineage reference.

This capability records governed Course-definition lineage. A Course or
CourseVersion does not imply publication, approval, qualification, delivery,
enrolment, learning, completion, assessment, certification, educator status,
payment, value, quality, readiness, or fitness for use.

S014 advances the INTEVIA fractal spine by giving EDUCATION a governed,
identity-bound definition aggregate without prematurely importing curriculum,
delivery, learner participation, or credential meaning.

## Human Decisions

Carmian Owen, Human Governor, directly retained and exercised authority over:

- the S014 scope and its explicit negative meanings;
- the implementation packet and exact sixteen-path boundary;
- correction of the two blocking and four material review findings;
- the bounded guardian-and-evidence experiment;
- stopping the expanded-QA execution when its cost and process risk became
  disproportionate to the pre-alpha product question;
- preserving the failed expanded-QA checkout and evidence rather than
  rewriting its history;
- restoring the exact pre-additional-QA candidate as the S014 closure basis;
- treating the platform-specific migration raw identity and aggregate
  implementation identity as lineage metadata rather than non-portable or
  non-derivable executable gates;
- deferring the remaining `MAT-S014-01` assurance-completeness residuals to
  IDOP v0.9.6; and
- requiring a bounded read-only closure analysis before this Datacron MCP.

Those decisions did not waive an established product failure. They separated
the accepted product-scope evidence from a later assurance experiment whose
guardians and evidence package did not fully discriminate every claim they
purported to prove.

## Exact Implementation Boundary

The accepted implementation changes exactly:

```text
core/models.py
core/migrations/0018_s014_education_course_foundation.py
src/intevia/services/education_course_authority.py
src/intevia/services/education_course_contract.py
src/intevia/services/education_course_service.py
src/intevia/services/education_course_read_service.py
tests/test_education_course_authority.py
tests/test_education_course_contract.py
tests/test_education_course_models.py
tests/test_education_course_service.py
tests/test_education_course_readback.py
tests/test_education_course_postgresql.py
tests/test_education_course_migrations.py
tests/test_s007_postgresql.py
tests/test_service_activity_migrations.py
tests/test_service_activity_models.py
```

No UI, API, publication, qualification, enrolment, curriculum, delivery,
assessment, evidence, certification, payment, cross-organism visibility, or
operational authority-provider capability is introduced.

## Aggregate, Authority, And Readback

The implementation introduces `Course` and `CourseVersion` through migration
`core.0018_s014_education_course_foundation`.

Governed create and append commands:

- require an active Human actor and active credential;
- bind action, actor, access epoch, target, database alias, request reference,
  idempotency key, evaluation time, and authority result;
- validate exact structured authority echoes before interpretation;
- fail closed on denial, malformed evidence, alias mismatch, stale head,
  cross-epoch replay, payload conflict, or corrupt lineage;
- preserve exact first-execution results on valid replay; and
- use database-alias-safe locking, reload, and current-pointer advancement.

Course definition values are normalised and canonically encoded. Time is UTC
with explicit effective-awareness qualification and fixed-width year handling.
Malformed canonical values, digests, authority evidence, or stored lineage are
reported without silent repair.

Readback is creator-only in S014. It reconstructs and validates current,
exact-version, and full-lineage projections before returning closed immutable
DTOs. Provider-authorised authorship of a later version does not manufacture
creator read access.

## Finding Reconciliation

```text
BLK-S014-01: CLOSED
BLK-S014-02: CLOSED
MAT-S014-02: CLOSED
MAT-S014-03: CLOSED
MAT-S014-04: CLOSED UNDER HUMAN RECONCILIATION
MAT-S014-01: DEFERRED TO IDOP v0.9.6
```

`BLK-S014-01` was closed by requiring exact authority response/refusal class
and field-type qualification before equality and interpretation.

`BLK-S014-02` was closed by database-alias-safe reload and current-pointer
advancement.

`MAT-S014-02` was closed by routing reconstruction and canonicalisation
failures to the governed lineage error without repair.

`MAT-S014-03` was closed through effective timezone awareness, exact UTC
conversion, fixed-width year behaviour, and digest guardians.

`MAT-S014-04` was closed under Human reconciliation when all sixteen clean Git
blobs and the exact prospective tree reproduced. The platform-specific raw
CRLF identity of migration `0018` remains lineage metadata, not a portable
execution predicate.

`MAT-S014-01` remains open only as an assurance-completeness residual for the
expanded guardian matrix and its raw evidence. It did not establish a
remaining defect in the committed Course implementation. It is preserved for
the separate IDOP v0.9.6 development and review unit and is not represented as
passed, closed, waived, or silently converted into S014 product evidence.

## Validation And Repository Evidence

The exact implementation tree qualified through:

```text
Clean Git blob identities: 16/16
Applicable raw SHA-256 identities: 15/15
Prospective tree: exact 8562e29fdc3d150b38cc5eb14eb3a194858bff96
Focused S014 gate: 50/50, 0 failures, 0 errors, 0 skips
PostgreSQL compatibility: 49/49, 0 failures, 0 errors, 0 skips
Full PostgreSQL regression: 663/663, 0 failures, 0 errors, 0 skips
Django system check: PASS
Migration drift check: PASS - no changes detected
Staged path set: exactly 16
Commit tree: exact governed tree
Push: one normal non-force Push to origin/main
Remote readback: exact implementation commit match
```

The qualified state-aware closeout return is:

```text
IDOP_V0_9_5_S014_STATE_AWARE_CLOSEOUT_RETURN_v1.md
raw byte length: 7823
SHA-256: c0e7065367d34771cf770eee46fb81e7941e289e0ccfb3a87af92a63bf81e103
disposition: S014_COMMIT_PUSH_COMPLETE_HUMAN_CLOSURE_READY
```

The bounded read-only closure analysis is:

```text
IDOP_V0_9_5_S014_BOUNDED_READ_ONLY_CLOSURE_ANALYSIS_RETURN_v1.md
raw byte length: 7144
SHA-256: 998a5becc75774499f8671a79dc08be247dec19c37d268f0a1fc00ffafee138c
disposition: S014_CLOSURE_ANALYSIS_PASS_DATACRON_READY
implementation correction required: NO
```

Producer-executed runtime evidence remains producer-executed evidence. The
closure analysis independently reconciled the available artefacts and live
repository state read-only; it did not rerun the product tests.

## Recovery And Truthful Process Lineage

The later expanded-QA experiment attempted to prove substantially stronger
guardian-discrimination and evidence-completeness claims than were required by
the original pre-alpha S014 closure basis. It exposed useful assurance-design
questions, but it also introduced process defects and disproportionate cost.

The experiment and recovery lineage are preserved truthfully:

1. the exact pre-additional-QA candidate had already passed its original
   focused, compatibility, and full-regression basis;
2. the expanded-QA branch attempted deeper guardian and evidence assurance;
3. review found incomplete or non-discriminating evidence for several matrix
   claims;
4. the Human Governor stopped that experiment and deferred its unresolved
   assurance residuals;
5. the failed checkout and its sixteen visible files were preserved untouched;
6. a separate clean Git worktree reconstructed and byte-qualified the exact
   pre-additional-QA candidate;
7. an over-strict recovery contract misread `49/49` as an unqualified
   zero-skip predicate and produced a needless stop after thirteen
   PostgreSQL-only methods ran against the wrong connection vendor;
8. state-aware reconciliation proved those skips were an environment-routing
   defect, not a product failure;
9. the process qualified PostgreSQL explicitly, then passed `49/49` and the
   remaining `663/663` regression; and
10. the exact governed tree was committed and pushed once.

Earlier HOLDs and failed experimental results remain truthful at issue. Later
success does not rewrite them as passes. Equally, a specification-only or
environment-only mismatch is not promoted into a product defect.

## IDOP And HAT Significance

S014 demonstrates both the strength of Human-governed AI work and an important
limit on assurance depth during pre-alpha development.

IDOP v0.9.5, explicit Human authority, role separation, exact carrier identity,
durable evidence, and same-session recovery enabled the programme to recover
from repeated channel and transport failures without losing the accepted
candidate or allowing AI nodes to manufacture authority. The Human remained
the governor of meaning, scope, mutation, stop, deferral, and closure.

The experiment also showed that assurance can create more risk than it removes
when it is not proportional to lifecycle stage and consequence. A pre-alpha
Slice primarily needs to establish that the architecture works well enough to
continue building. Requiring every assurance claim to be adversarially
discriminated, exhaustively evidenced, repeatedly packaged, and independently
re-reviewed can be a milestone or release-readiness activity rather than a
routine Slice gate.

Inputs retained for IDOP v0.9.6 and INTEVIA v1.1 include:

- stage-appropriate assurance proportional to consequence and uncertainty;
- terminal HOLD only for genuine uncertainty, divergence, missing authority,
  or unsafe state—not for mechanically reconcilable contract defects;
- explicit separation of product failure, evidence incompleteness,
  environment misconfiguration, transport failure, and specification error;
- state-aware continuation from qualified checkpoints without reconstruction;
- same-session continuity by default, with repository isolation supplied by
  clean worktrees rather than forced contextual reboot;
- self-contained handoffs that carry readiness evidence forward;
- automatic durable preservation before user-interface display;
- fewer Human download-upload-paste loops; and
- recovery and orchestration failures contained beneath the Human experience.

This Datacron records those observations. It does not itself amend or promote
IDOP v0.9.5, create IDOP v0.9.6, or define INTEVIA v1.1.

## Cost And Time Evidence

Carmian Owen's Human-reported provisional model-cost accounting at S014
implementation Commit/Push was:

```text
Approximate S014 model spend: $55
Approximate expanded-QA experiment and recovery share: $30-$35
Approximate productive implementation and closeout remainder: $20-$25
```

These are operational estimates, not audited financial accounts. They are
preserved because the cost ratio materially informs stage-appropriate
assurance design.

The closure-day Clockify screenshot showed:

```text
Today: 07:26:14
This week: 56:12:16
```

Clockify remains the Human's authoritative time ledger. Those screenshot
values are not asserted as the exact S014 duration, and no fully reconciled
Slice-time total is claimed here.

## Retained Boundaries And Deferrals

This Datacron does not:

- constitute Human implementation acceptance, Datacron acceptance, or S014
  closure merely because it exists in the repository;
- claim that the expanded-QA experiment or `MAT-S014-01` passed;
- require another S014 code correction, test run, SO-PRO review, Adversator
  review, recovery carrier, or implementation Commit;
- treat the preserved sixteen-file failed-QA checkout as unpublished S014 work;
- activate an operational authority provider or create operational Course data;
- apply migration `0018` outside qualified test environments;
- deploy, publish, release, tag, or claim production readiness;
- establish security, privacy, compliance, accessibility, performance,
  longitudinal, operational, or Human-usability readiness;
- create publication, qualification, enrolment, curriculum, delivery,
  assessment, evidence, certification, payment, or cross-organism semantics;
- upgrade producer-executed runtime evidence into independent reproduction;
- erase, waive, close, or misrepresent the v0.9.6 assurance residuals;
- reopen S013;
- authorise S015 or any future Slice;
- amend or promote IDOP; or
- turn possession, storage, copying, quotation, forwarding, replay, or model
  repetition into Human authority.

Historical evidence is lineage, not continuing permission. Every future
consequential action remains subject to current Human authority and its owning
domain.

## Repository And MCP Boundary

The exact repository destination for the separately authorised Datacron MCP is:

```text
docs/holocron/datacrons/D-S014-GOVERNED-EDUCATION-COURSE-DEFINITION.md
```

The authorised commit subject is:

```text
docs(datacron): record governed education course definition
```

The MCP may add exactly this one documentation path to `main` from exact
implementation baseline `4530d593127b163c1bafb8d79f03349c61d9335a`, then
perform one normal non-force Push and receiver-direct remote readback. It may
not alter the implementation, tests, migrations, preserved failed checkout,
other documentation, or any lifecycle state.

After that Commit/Push, Carmian Owen must separately and directly issue the
final Human acceptance and Datacron closure marker bound to both the S014
implementation commit and the exact Datacron commit/evidence.

## Pending Human Disposition

```text
S014 implementation Commit/Push: COMPLETE
Closure analysis: PASS - DATACRON READY
Datacron repository Commit/Push: PENDING MCP
Human implementation acceptance: NOT YET ISSUED
Human Datacron acceptance: NOT YET ISSUED
S014 closure: NOT YET ISSUED
MAT-S014-01: DEFERRED TO IDOP v0.9.6
Expanded-QA experiment: PRESERVED, NOT CLAIMED PASSED
```

The next authority event after the bounded Datacron MCP is the direct Human
acceptance and Datacron closure marker. This document cannot issue that marker
for Carmian Owen.
