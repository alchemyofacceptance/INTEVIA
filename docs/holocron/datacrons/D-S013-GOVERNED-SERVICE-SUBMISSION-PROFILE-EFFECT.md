# D-S013-GOVERNED-SERVICE-SUBMISSION-PROFILE-EFFECT

## Status

```text
Type: HOLOCRON Datacron Record
Purpose: S013 lineage preservation
Phase: Development-phase implementation record
Status: Repository lineage record pending Human Datacron acceptance
Runtime effect: None
```

This Datacron records the governed S013 implementation slice that introduces
SERVICE-qualified, neutral `PROFILE_EFFECT` proposal and projection lineage.
It preserves the exact bounded execution outcome and the validation evidence
that was actually produced in this session.

Committing this exact record does not constitute Human Datacron acceptance,
close S013, activate a downstream profile meaning surface, deploy INTEVIA, or
substitute for PostgreSQL qualification in an environment where PostgreSQL was
not provisioned.

## Identity

```text
Datacron: D-S013-GOVERNED-SERVICE-SUBMISSION-PROFILE-EFFECT
Slice: S013 - Governed Service Submission Profile Effect
Domain owner: SERVICE
Human Governor: Carmian Owen
Repository: https://github.com/alchemyofacceptance/INTEVIA.git
Branch: candidate/s013-recovery-correction-20260728
Implementation baseline: 590b0e527c2abc71053a5dbd26f8693953f1c845
Environment: internal-pre-alpha
Controlling practice: IDOP machine-readable family
```

## Capability Meaning

S013 allows SERVICE to convert one exact qualified S012 service-submission
occurrence into a neutral profile-effect proposal lineage, preserve subsequent
proposal corrections, preserve separate projection dispositions, and expose a
subject-private readback for the resulting lineage.

The sole S013 source anchor is the exact recorded `SUBMIT_WORK` transition.
No other S012 transition, including review, completion, cancellation, or
replay, creates S013 meaning.

Within every governed S013 occurrence, `subject == proposer == actor ==
immutable assignee`.

Physical placement within the Django `core` application is an implementation locality only. `PROFILE_EFFECT` is a distinct cross-domain governance seam. CORE/Identity does not own or interpret profile meaning. SERVICE owns exact source qualification and authorised proposal creation. `PROFILE_EFFECT` owns only neutral proposal and disposition lineage. Any downstream profile meaning, presentation, or use remains with the separately authorised receiving domain.

The slice records neutral proposal and disposition facts only. It does not
assign profile meaning, rank people, score performance, infer behavioural
qualities, or widen visibility beyond the subject-private readback boundary.
The fixed lifecycle actions are exactly `CREATE_PROPOSAL`, `VOID_PROPOSAL`,
`SUPERSEDE_PROPOSAL`, `AUTHORISE_PROJECTION`, `DECLINE_PROJECTION`, and
`WITHDRAW_PROJECTION`.
Readback is subject-private and default-deny. No outward readback, browse,
count, or comparison surface is authorised in S013.
Later authorised presentation or premium-facing language remains under LO
sovereignty and is outside S013.
Real-Human activation is prohibited. This slice remains limited to synthetic
fixtures until a separately governed activation, retention, and erasure design
exists.
Future `ENGAGEMENT` and `CARE` units remain separately governed. Any future
`REVIEW_WORK` relevance is a distinct separately authorised slice and has no
S013 meaning here.
Any future CARE comparison or observation is non-binding reflection only.
CARE may surface a potential contradiction or risk of misinterpretation to
the applicable LO, Circle Coordinator, or Human authority. CARE cannot
rewrite, suppress, block, or overrule an LO or Human decision. No CARE runtime
is implemented or activated by S013.
Deferred successor work remains separate: Governed Identity Exit, Account
Deletion, Authorship Continuity and Erasure.

## Exact Implementation Boundary

The implemented S013 slice is bounded to these exact paths:

```text
core/models.py
src/intevia/services/service_activity_read_service.py
tests/test_service_activity_models.py
tests/test_service_activity_migrations.py
tests/test_s007_postgresql.py
core/migrations/0017_s013_profile_effect.py
src/intevia/services/profile_effect_contract.py
src/intevia/services/profile_effect_authority.py
src/intevia/services/profile_effect_service.py
src/intevia/services/profile_effect_read_service.py
tests/test_service_activity_qualification.py
tests/test_profile_effect_models.py
tests/test_profile_effect_authority.py
tests/test_profile_effect_service.py
tests/test_profile_effect_readback.py
tests/test_profile_effect_migrations.py
tests/test_profile_effect_postgresql.py
docs/holocron/datacrons/D-S013-GOVERNED-SERVICE-SUBMISSION-PROFILE-EFFECT.md
```

The slice introduces exactly three persistence models, three service classes,
six command methods, one subject-private read surface, one migration leaf, one
S012 qualification seam guardian, one S013 PostgreSQL catalogue guardian, and
the live Identity foreign-key catalogue expansion from 41 to 45 external
references.

## Validation Evidence

Earlier `88/88` focused evidence belongs to an earlier snapshot and is
historical only. The preserved recovery report relays later `22/22` S013
PostgreSQL and `60/60` compatibility results; this correction packet did not
independently rerun those results. The only prior `592/592` full-regression
PASS also belongs to a superseded snapshot. The recovery-snapshot 598-test
regression was interrupted and is not PASS evidence.

Those superseded and interrupted results are not current qualification
evidence. They are preserved here solely as execution history.

### Final-Convergence Qualification Ledger

The final-convergence unit requalified the preserved candidate at entry commit
`d88862e963454c96a5ec64792d823702209e8beb`, inherited Q1-Q5 only after the
complete cryptographic snapshot matched, and made one Human-authorised
test-only correction. No production, migration, model, configuration, service,
dependency, architecture, or second test file changed in that correction.

```text
Q1_ONE_FIVE_PATH_CORRECTION: PASS — inherited, not repeated
Q2_STATIC_AND_FOCUSED_SQLITE: PASS — inherited 24/24, zero failures/errors/skips
Q3_POSTGRESQL_MIGRATION_REHEARSAL: PASS — inherited 6/6, zero failures/errors/skips
Q4_FOCUSED_POSTGRESQL: PASS — inherited 25/25, zero failures/errors/skips
Q5_COMPATIBILITY: PASS — inherited 110/110, zero failures/errors/skips
Q6_INDEPENDENT_READ_ONLY_AUDIT: FAIL — two mandatory findings preserved
Q6_FINDING_1: projection unknown-constraint guardian absent
Q6_FINDING_1_DISPOSITION: corrected by exact Human authority in final convergence
Q6_FINDING_2: auditor did not independently recompute all snapshot qualifiers
Q6_FINDING_2_DISPOSITION: discharged by the Human Governor because the parent independently recomputed the complete cryptographic snapshot immediately before audit
FINAL_FOCUSED_POSTGRESQL: PASS — 26/26, zero failures/errors/skips
FINAL_FULL_POSTGRESQL_REGRESSION: PASS — 613/613, zero failures/errors/skips
FINAL_EVIDENCE_REBINDING: PASS_ONE_REGRESSION_SUFFICIENT
IMPLEMENTATION_COMMIT: PENDING
FETCH: PENDING
PUSH: PENDING
```

The added guardian is
`test_unknown_projection_constraint_propagates_without_winner_recovery`. It
enters the projection integrity route with an unknown constraint outside the
projection allow-set and proves that the same `IntegrityError` object
propagates without winner recovery. Compile/import passed before PostgreSQL
testing. The complete focused module discovered and executed 26 tests in
29.696 seconds (38.080 seconds command duration).

The complete `tests` label was predeclared at 613 tests with an empty expected
skip set. The exact Node witness was bound in the shell and canonical Python
process before discovery and execution:

```text
C:\Users\Carewen\AppData\Local\Microsoft\WinGet\Packages\OpenJS.NodeJS.LTS_Microsoft.Winget.Source_8wekyb3d8bbwe\node-v24.18.0-win-x64\node.exe
Node version: v24.18.0
Regular/non-reparse: true/true
```

The one full PostgreSQL regression discovered, executed, and passed all 613
tests in 351.962 seconds (360.008 seconds command duration), with zero
failures, errors, or skips. Its test database was destroyed. No regression was
repeated.

### Candidate Snapshot And Containment

The qualification snapshot retained exactly five unstaged candidate paths on
entry commit `d88862e963454c96a5ec64792d823702209e8beb`. After the one
authorised guardian, the deterministic binary diff was 26,531 bytes with
SHA-256
`f187ecc7b3f55c6f516122bcc100417811da33c6561910f367cb0d3de2a7c91e`.
The tested guardian file was 55,209 bytes with SHA-256
`ebd73164e846a89c6327269e63239a0156936cdb3fb16f02ae23de5465874c1c`.
The other four candidate blobs remained byte-identical to the inherited
snapshot.

The full-regression snapshot bound 164 tracked production, migration, test,
and configuration blobs. No collected test reads, parses, or asserts on this
Datacron. The post-regression ledger change is therefore evidence-only; all
164 qualified blobs must remain byte-identical to the tested snapshot.

The fresh PostgreSQL 16 substrate used image ID `33f923b05f64`, loopback-only
publication, tmpfs data, and zero mounts or volumes. Focused and full test
databases were destroyed after their runs. Final run-container and process
containment remains a required pre-Commit gate.

Final-convergence activity used zero subagents, zero auditors, zero nested
children, one test-only correction pass, one focused PostgreSQL execution, one
full regression, and zero product-test retries. The prior Q6 auditor remains
preserved historical evidence and was not repeated. One discovery helper
omitted `DJANGO_SETTINGS_MODULE` and was corrected before collection; one
snapshot-manifest display pipeline had a PowerShell parse defect and was
corrected before execution. Neither mechanic affected repository, tests,
database, Docker, or Git state.

Commit, fetch, Push, receiver-direct remote verification, and final resource
containment remain pending at the time this ledger is written. This evidence
does not qualify a merge, constitute implementation acceptance, accept or
close S013, deploy or publish INTEVIA, activate Real-Human use, or authorise
S014 substantive work. Final authority remains with Carmian Owen, Human
Governor.

## Architectural Notes Preserved

- Source truth remains owned by S012 qualification, not reconstructed from
  unqualified caller input.
- Proposal authority and projection authority remain separate domains.
- `SUBMIT_WORK` remains the sole source anchor for S013 proposal creation.
- The only lifecycle actions are `CREATE_PROPOSAL`, `VOID_PROPOSAL`,
  `SUPERSEDE_PROPOSAL`, `AUTHORISE_PROJECTION`, `DECLINE_PROJECTION`, and
  `WITHDRAW_PROJECTION`.
- Replay validates stored authority evidence rather than trusting fresh
  authority output to describe historical state.
- Subject visibility is exact-epoch, subject-private, and default-deny.
- Neutral lineage remains append-only and physically local to `core` without
  transferring semantic ownership to CORE/Identity.
- S013 provides append-only and immutable-occurrence behavior through the
  governed service surface and declared database constraints. It does not
  claim absolute physical database immutability. Privileged database bypass,
  operator-level mutation, backup restoration, and future separately
  authorised erasure mechanisms remain outside this governed-service
  guarantee.
- Later presentation language remains under LO sovereignty.
- Future `ENGAGEMENT` and `CARE` units remain separately authorised.
- Future `REVIEW_WORK` relevance remains separately authorised.
- Governed Identity Exit, Account Deletion, Authorship Continuity, and Erasure
  remain deferred.
- Real-Human activation remains prohibited in this slice.