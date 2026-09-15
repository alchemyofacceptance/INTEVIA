# INTEVIA v1.0 — Current Implementation Crosswalk

Qualified repository ref: `4282ebcbc6c3b942c0b6c5efa8b3ff1a01ad73e0`  
Last verified: 2026-09-15  
Document role: derivative evidence-state navigation  
Authority effect: none

## What this crosswalk cannot establish

This document creates no authority. It does not replace or amend a Datacron, prove current test execution, independently reproduce historical evidence, establish Human acceptance, close a finding, validate a release, or establish deployment, certification, external validation, or product completeness.

Repository presence, test-definition presence, recorded execution, independent reproduction, Human acceptance, unresolved findings, and release state are separate evidence classes. They must not be compressed into one status impression.

## Source precedence

1. exact repository bytes at the qualified ref for file presence;
2. exact execution evidence for the recorded run only;
3. exact Slice evidence for its declared technical boundary;
4. exact Human-issued marker for acceptance or closure;
5. this crosswalk only as derivative navigation.

## Shared deployment and external-validation boundary

At this qualified ref, every capability block is internal pre-alpha; no capability block states a different field-10 status. A citation, implementation path, test definition, recorded execution, Human acceptance, or silence does not by itself override this boundary. Any different status requires both an express field-10 statement and an exact Human-issued source that authorises that status within its declared boundary. Deployment, release, certification, and broad external validation are otherwise not established.

## Capability blocks

### 1. Contribution and governed knowledge lineage

**1 — Capability family and bounded claim:** Paths are present for contribution lifecycle, authority, correction, privacy, archive, and service behaviour.

**2 — Qualified repository ref:** `4282ebcbc6c3b942c0b6c5efa8b3ff1a01ad73e0`.

**3 — Implementation paths present:** `core/models.py`; `src/intevia/core/contribution.py`; `src/intevia/services/contribution_authority.py`; `src/intevia/services/contribution_service.py`.

**4 — Test-definition paths present:** `tests/test_contribution_*.py` and related service tests. Presence is not execution.

**5 — Last recorded execution evidence:** Recorded execution is not restated here; inspect the linked S003 Datacron and its exact sources.

**6 — Slice-specific evidence source:** [`D-S003-GOVERNED-KNOWLEDGE-LINEAGE.md`](../holocron/datacrons/D-S003-GOVERNED-KNOWLEDGE-LINEAGE.md).

**7 — Independent reproduction:** Not established by this crosswalk.

**8 — Exact Human acceptance or closure source:** No acceptance or closure inference is made unless the linked source contains an exact Human-issued event.

**9 — Unresolved findings and deferrals:** No current-Human-acceptance inference is made by repository presence.

**10 — Deployment, release, certification, and external-validation state:** Internal pre-alpha. The [shared boundary](#shared-deployment-and-external-validation-boundary) applies; this block establishes no deployment, release, certification, or broad external-validation state.

**11 — Explicit non-claims:** No completeness, deployment, or universal workflow claim.

**12 — Last verification date:** 2026-09-15.

**13 — Staleness trigger:** Any change to the qualified ref, cited evidence, Human decision, or unresolved-finding state.

### 2. Identity, authentication, attendance, and direct self-registration

**1 — Capability family and bounded claim:** Paths are present for a bounded Identity foundation, authentication shell, attendance, personal event home, and direct self-registration.

**2 — Qualified repository ref:** `4282ebcbc6c3b942c0b6c5efa8b3ff1a01ad73e0`.

**3 — Implementation paths present:** `core/identity.py`; `core/models.py`; `core/forms.py`; `core/views.py`; `src/intevia/services/identity_service.py`; event attendance and self-registration services.

**4 — Test-definition paths present:** `tests/test_s007_*.py`; `tests/test_event_attendance_*.py`; `tests/test_s009_personal_event_home.py`; `tests/test_s010_direct_self_registration*.py`. Presence is not execution.

**5 — Last recorded execution evidence:** Historical recorded results are carried only by the relevant exact evidence sources; no current rerun is claimed.

**6 — Slice-specific evidence source:** [`D-S009-FIRST-HUMAN-LANDING-PAD.md`](../holocron/datacrons/D-S009-FIRST-HUMAN-LANDING-PAD.md) and [`D-S010-GOVERNED-DIRECT-SELF-REGISTRATION.md`](../holocron/datacrons/D-S010-GOVERNED-DIRECT-SELF-REGISTRATION.md).

**7 — Independent reproduction:** Not established by this crosswalk.

**8 — Exact Human acceptance or closure source:** No acceptance or closure inference is made unless the linked source contains an exact Human-issued event.

**9 — Unresolved findings and deferrals:** Broader account lifecycle, SSO, organisation administration, and production authentication are not established.

**10 — Deployment, release, certification, and external-validation state:** Internal pre-alpha. The [shared boundary](#shared-deployment-and-external-validation-boundary) applies; this block establishes no deployment, release, certification, or broad external-validation state.

**11 — Explicit non-claims:** No general identity-platform or operational-security claim.

**12 — Last verification date:** 2026-09-15.

**13 — Staleness trigger:** Any change to the qualified ref, cited evidence, Human decision, or unresolved-finding state.

### 3. Events and governed registration

**1 — Capability family and bounded claim:** Paths are present for Event lifecycle, governed registration, attendance, and read services.

**2 — Qualified repository ref:** `4282ebcbc6c3b942c0b6c5efa8b3ff1a01ad73e0`.

**3 — Implementation paths present:** `core/models.py`; `src/intevia/services/event_service.py`; registration, attendance, and read services under `src/intevia/services/`.

**4 — Test-definition paths present:** `tests/test_events_*.py`; `tests/test_event_registration_*.py`; `tests/test_event_attendance_*.py`. Presence is not execution.

**5 — Last recorded execution evidence:** Recorded execution is not converted into a current-pass claim.

**6 — Slice-specific evidence source:** [`D-S006-GOVERNED-EVENT-REGISTRATION-FOUNDATION.md`](../holocron/datacrons/D-S006-GOVERNED-EVENT-REGISTRATION-FOUNDATION.md).

**7 — Independent reproduction:** Not established by this crosswalk.

**8 — Exact Human acceptance or closure source:** No acceptance or closure inference is made unless the linked source contains an exact Human-issued event.

**9 — Unresolved findings and deferrals:** No full event-management, ticketing, scheduling, or deployment claim.

**10 — Deployment, release, certification, and external-validation state:** Internal pre-alpha. The [shared boundary](#shared-deployment-and-external-validation-boundary) applies; this block establishes no deployment, release, certification, or broad external-validation state.

**11 — Explicit non-claims:** No complete Events-module claim.

**12 — Last verification date:** 2026-09-15.

**13 — Staleness trigger:** Any change to the qualified ref, cited evidence, Human decision, or unresolved-finding state.

### 4. Library resources and exact-version binding

**1 — Capability family and bounded claim:** Paths are present for governed Library resources and exact-version contracts, policy, service, and PostgreSQL-oriented tests.

**2 — Qualified repository ref:** `4282ebcbc6c3b942c0b6c5efa8b3ff1a01ad73e0`.

**3 — Implementation paths present:** `src/intevia/services/library_service.py`; `library_exact_version_contract.py`; `library_exact_version_policy.py`; related models and migrations.

**4 — Test-definition paths present:** `tests/test_library_*.py`. Presence is not execution.

**5 — Last recorded execution evidence:** Inspect the linked S003 and S011-A records for bounded historical execution claims.

**6 — Slice-specific evidence source:** [`D-S003-GOVERNED-KNOWLEDGE-LINEAGE.md`](../holocron/datacrons/D-S003-GOVERNED-KNOWLEDGE-LINEAGE.md) and [`D-S011A-GOVERNED-LIBRARY-EXACT-VERSION-CONTRACT.md`](../holocron/datacrons/D-S011A-GOVERNED-LIBRARY-EXACT-VERSION-CONTRACT.md).

**7 — Independent reproduction:** Not established by this crosswalk.

**8 — Exact Human acceptance or closure source:** No acceptance or closure inference is made unless the linked source contains an exact Human-issued event.

**9 — Unresolved findings and deferrals:** No complete library product, discovery system, or external content platform.

**10 — Deployment, release, certification, and external-validation state:** Internal pre-alpha. The [shared boundary](#shared-deployment-and-external-validation-boundary) applies; this block establishes no deployment, release, certification, or broad external-validation state.

**11 — Explicit non-claims:** No operational completeness or current-pass inference.

**12 — Last verification date:** 2026-09-15.

**13 — Staleness trigger:** Any change to the qualified ref, cited evidence, Human decision, or unresolved-finding state.

### 5. Services, activity orchestration, and event-resource readback

**1 — Capability family and bounded claim:** Paths are present for governed Service foundations, activity assignment/delivery/readback, and Event-resource relationship contracts and readback.

**2 — Qualified repository ref:** `4282ebcbc6c3b942c0b6c5efa8b3ff1a01ad73e0`.

**3 — Implementation paths present:** Service, activity, authority, and Event-resource relationship modules under `src/intevia/services/`; related `core/models.py` and migrations.

**4 — Test-definition paths present:** `tests/test_service_*.py`; `tests/test_event_resource_relationship_*.py`. Presence is not execution.

**5 — Last recorded execution evidence:** Historical results remain scoped to the linked records and their named repository states.

**6 — Slice-specific evidence source:** [`D-S004-GOVERNED-SERVICE-FOUNDATION.md`](../holocron/datacrons/D-S004-GOVERNED-SERVICE-FOUNDATION.md), [`D-S011B-GOVERNED-EVENT-RESOURCE-LINKING-AND-READBACK.md`](../holocron/datacrons/D-S011B-GOVERNED-EVENT-RESOURCE-LINKING-AND-READBACK.md), and [`D-S012-GOVERNED-SERVICE-ACTIVITY-ASSIGNMENT-DELIVERY-AND-READBACK.md`](../holocron/datacrons/D-S012-GOVERNED-SERVICE-ACTIVITY-ASSIGNMENT-DELIVERY-AND-READBACK.md).

**7 — Independent reproduction:** Not established by this crosswalk.

**8 — Exact Human acceptance or closure source:** No acceptance or closure inference is made unless the linked source contains an exact Human-issued event.

**9 — Unresolved findings and deferrals:** No general workflow engine, marketplace, payment, or cross-organism capability.

**10 — Deployment, release, certification, and external-validation state:** Internal pre-alpha. The [shared boundary](#shared-deployment-and-external-validation-boundary) applies; this block establishes no deployment, release, certification, or broad external-validation state.

**11 — Explicit non-claims:** No complete Service-module or deployment claim.

**12 — Last verification date:** 2026-09-15.

**13 — Staleness trigger:** Any change to the qualified ref, cited evidence, Human decision, or unresolved-finding state.

### 6. Service-submission profile effect

**1 — Capability family and bounded claim:** Paths are present for bounded profile-effect authority, contract, service, and readback following governed service submission.

**2 — Qualified repository ref:** `4282ebcbc6c3b942c0b6c5efa8b3ff1a01ad73e0`.

**3 — Implementation paths present:** `src/intevia/services/profile_effect_authority.py`; `profile_effect_contract.py`; `profile_effect_service.py`; `profile_effect_read_service.py`; migration `0017`.

**4 — Test-definition paths present:** `tests/test_profile_effect_*.py`. Presence is not execution.

**5 — Last recorded execution evidence:** The S013 record is the route to bounded execution and reconciliation evidence; no current rerun is claimed.

**6 — Slice-specific evidence source:** [`D-S013-GOVERNED-SERVICE-SUBMISSION-PROFILE-EFFECT.md`](../holocron/datacrons/D-S013-GOVERNED-SERVICE-SUBMISSION-PROFILE-EFFECT.md).

**7 — Independent reproduction:** Not established by this crosswalk.

**8 — Exact Human acceptance or closure source:** No acceptance or closure inference is made unless the linked source contains an exact Human-issued event.

**9 — Unresolved findings and deferrals:** No general reputation, scoring, radiance, or recognition-system claim.

**10 — Deployment, release, certification, and external-validation state:** Internal pre-alpha. The [shared boundary](#shared-deployment-and-external-validation-boundary) applies; this block establishes no deployment, release, certification, or broad external-validation state.

**11 — Explicit non-claims:** No universal profile-effect semantics or operational readiness.

**12 — Last verification date:** 2026-09-15.

**13 — Staleness trigger:** Any change to the qualified ref, cited evidence, Human decision, or unresolved-finding state.

### 7. Education Course-definition foundation

**1 — Capability family and bounded claim:** Paths are present for a bounded governed Course aggregate, immutable versions, authority, create/append service, and creator-only readback.

**2 — Qualified repository ref:** `4282ebcbc6c3b942c0b6c5efa8b3ff1a01ad73e0`.

**3 — Implementation paths present:** `core/models.py`; migration `core/migrations/0018_s014_education_course_foundation.py`; four `education_course_*` service modules.

**4 — Test-definition paths present:** Seven `tests/test_education_course_*.py` paths plus the compatibility paths named in the S014 record. Presence is not execution.

**5 — Last recorded execution evidence:** The S014 Datacron records focused 50/50, PostgreSQL compatibility 49/49, and full PostgreSQL regression 663/663 at its named implementation state. The static baseline also contains 663 textual test-definition matches; these numerically equal values are different evidence classes and do not establish that every static match was the executed regression selection.

**6 — Slice-specific evidence source:** [`D-S014-GOVERNED-EDUCATION-COURSE-DEFINITION.md`](../holocron/datacrons/D-S014-GOVERNED-EDUCATION-COURSE-DEFINITION.md).

**7 — Independent reproduction:** Not established by this crosswalk.

**8 — Exact Human acceptance or closure source:** No acceptance or closure inference is made unless the linked source contains an exact Human-issued event.

**9 — Unresolved findings and deferrals:** `MAT-S014-01` remains deferred to IDOP v0.9.6. The record at this baseline says it is pending direct Human implementation acceptance, Datacron acceptance, and closure.

**10 — Deployment, release, certification, and external-validation state:** Internal pre-alpha. The [shared boundary](#shared-deployment-and-external-validation-boundary) applies; this block establishes no deployment, release, certification, or broad external-validation state.

**11 — Explicit non-claims:** No curriculum delivery, class delivery, enrolment, learning, completion, assessment, certification, educator qualification, payment, publication, or operational Course claim.

**12 — Last verification date:** 2026-09-15.

**13 — Staleness trigger:** Any change to the qualified ref, cited evidence, Human decision, or unresolved-finding state.

### 8. Living Organism substrate and the INTEVIA Lineage-Chain (Slice S015, packets PKT-A-2 and PKT-A-3)

**1 — Capability family and bounded claim:** Paths are present for organism, circle and membership anchors; twelve append-only governed event chains with a canonical byte form, per-row fingerprints and a bitemporal fold (PKT-A-2); and the INTEVIA Lineage-Chain (ILC) contract laid onto those chains — actor states, parts and commitments, an identity-resolution table with an append-only severance ledger (PKT-A-3). Both packets are designated, implemented and landed; neither is accepted. Slice S015 is open.

**2 — Qualified repository ref:** `4282ebcbc6c3b942c0b6c5efa8b3ff1a01ad73e0`.

**3 — Implementation paths present:** `core/models.py`; `core/migrations/0019_s015_living_organism_foundation.py`; `0020_s015_genesis_identity_completeness.py`; `0021_s015_recorded_chain_and_bitemporal_fold.py`; `0022_s015_governed_chain_contract.py`; `core/organism_contract.py`; `src/intevia/services/organism_authority.py`; `organism_membership_service.py`; `vectors/S015_CANONICAL_FORM_s015f3_RULE_v0_1.md` and `vectors/s015f3_fixed_vectors.json`. Migration `0022` declares no Django model state and adds no ORM classes; the four tables it creates and the columns it alters are not represented in `core/models.py`.

**4 — Test-definition paths present:** `tests/test_s015_*.py`; `tests/s015_0021_support.py`; `tests/test_s015_0021_negative_direct_sql.py`; `tests/test_s015_0021_positive_controls.py`; `tests/test_s015_postgresql_guardians.py`; `core/tests/test_s015_0022_contract.py`. Presence is not execution. The PKT-A-3 Datacron records that `core/tests/test_s015_0022_contract.py` was not executed against the final candidate on the record and, as committed, contradicts migration `0022` on the resolution table's columns and on the second-layer preimage function's behaviour.

**5 — Last recorded execution evidence:** The PKT-A-2 Datacron records 120/120 (78 negative, 34 positive, 8 guardian) on a fresh PostgreSQL 17.10 throwaway at implementation commit `859960d8`, and 163 triggers against a baseline of 83. The PKT-A-3 Datacron records gates 2–7 passed at Python 3.12.10 / PostgreSQL 17.10 against the verified candidate, every behavioural scenario as the non-superuser production role, with verification instruments held outside the repository. Both are recorded results at their named states; neither is a current-pass claim, and neither has been independently reproduced.

**6 — Slice-specific evidence source:** [`D-S015-PKT-A-2-GOVERNED-RECORDED-CHAIN-AND-BITEMPORAL-FOLD.md`](../holocron/datacrons/D-S015-PKT-A-2-GOVERNED-RECORDED-CHAIN-AND-BITEMPORAL-FOLD.md) and [`D-S015-PKT-A-3-INTEVIA-LINEAGE-CHAIN-ILC.md`](../holocron/datacrons/D-S015-PKT-A-3-INTEVIA-LINEAGE-CHAIN-ILC.md) (v0.8, a corrected record whose corrections are marked and dated).

**7 — Independent reproduction:** Not established by this crosswalk. An external byte-level read of the public repository on 2026-09-15 is recorded in the PKT-A-3 Datacron as the source of its corrections; it executed no tests.

**8 — Exact Human acceptance or closure source:** None. Implementation acceptance, Datacron acceptance and packet closure for both packets are recorded in each Datacron's status block as not issued. PKT-A acceptance is not on the path forward, by Human ruling recorded in the PKT-A-2 Datacron.

**9 — Unresolved findings and deferrals:** Carried into PKT-B by Human ruling: no second-layer commitment verification (`U-14`; the preimage function exists and refuses); the schema cannot record an act taken by an Organism as an Organism (`BD-1`); admission policy on the identity-resolution table unruled (`U-6`); the application connects as a superuser and the production-role split is not discharged (`F-U21l-04`); the identity split and its insert-only guardian ruled on 12 September are not delivered in `0022`; the `0022` contract test file contradicts the migration; how stored fingerprints remain verifiable after an event-table column addition is unestablished and is to be resolved before populated chains undergo further schema changes. Open design questions `U-1` to `U-14` and `R-a` to `R-d` travel with the design object; the §14.1 carry-forward gate has not been re-derived since the chain landed.

**10 — Deployment, release, certification, and external-validation state:** Internal pre-alpha. The [shared boundary](#shared-deployment-and-external-validation-boundary) applies; this block establishes no deployment, release, certification, or broad external-validation state. No Organism or Circle is populated.

**11 — Explicit non-claims:** No populated Organism; no provable erasure as landed; no tamper-evident second layer; no Organism-as-actor; no complete ILC; no general lineage platform.

**12 — Last verification date:** 2026-09-15.

**13 — Staleness trigger:** Any change to the qualified ref, cited evidence, Human decision, or unresolved-finding state — including the disposition of the items at field 9 in PKT-B.

## Static inventory note

At the qualified ref, `git ls-files 'test_*.py' '**/test_*.py' | sort -u | wc -l` reports 97 tracked paths. Because a git pathspec `*` matches across directory separators, that figure includes three files matched through the directory name `intevia/test_postgresql_backend/`; 94 of the 97 are files whose own name begins `test_` (92 under `tests/`, plus `core/tests/test_s015_0022_contract.py` and `intevia/test_settings.py`). Applying `^\s*(async\s+)?def\s+test_` to tracked Python files reports 805 textual test-definition matches. These are reproducible static inventory methods, not collection or execution, and neither figure is an executed-selection count.

A full line census of the repository at implementation commit `5eb7783`, by category, with per-file identities and the script that produced it, is carried in the PKT-A-3 Datacron at §2 and will be carried by each subsequent Datacron at its own landing commit.

## Staleness and maintenance

This crosswalk becomes stale when the qualified ref changes, a cited evidence object is superseded, an exact Human decision changes a boundary, or an unresolved finding changes state. Datacron narratives should be linked rather than copied or silently reinterpreted.
