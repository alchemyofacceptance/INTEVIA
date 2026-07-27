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
Branch: main
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

The following commands were executed in this session against
`intevia.test_settings`:

```text
python manage.py test tests.test_service_activity_qualification tests.test_profile_effect_authority tests.test_profile_effect_models tests.test_profile_effect_service tests.test_profile_effect_readback tests.test_profile_effect_migrations tests.test_service_activity_models tests.test_service_activity_migrations tests.test_service_activity_readback --settings=intevia.test_settings --verbosity=1
```

Observed result:

```text
Found 88 test(s).
Creating test database for alias 'default'...
System check identified no issues (0 silenced).
Ran 88 tests in 77.678s
OK
Destroying test database for alias 'default'...
```

The following additional qualification steps were also observed earlier in the
same bounded execution:

```text
python manage.py check --settings=intevia.test_settings
```

Observed result:

```text
System check identified no issues (0 silenced).
```

The PostgreSQL-only S013 guardian was added as
`tests/test_profile_effect_postgresql.py`. In the current environment,
PostgreSQL was not provisioned by design, so this Datacron does not claim a
PostgreSQL pass result that was not executed.

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
- Later presentation language remains under LO sovereignty.
- Future `ENGAGEMENT` and `CARE` units remain separately authorised.
- Future `REVIEW_WORK` relevance remains separately authorised.
- Governed Identity Exit, Account Deletion, Authorship Continuity, and Erasure
  remain deferred.
- Real-Human activation remains prohibited in this slice.