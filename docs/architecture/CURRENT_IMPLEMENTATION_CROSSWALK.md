# INTEVIA v1.0 — Current Implementation Crosswalk

Qualified repository ref: `1aed0f4da88209d5298e40867b08505661cfd451`  
Last verified: 2026-07-31  
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

**2 — Qualified repository ref:** `1aed0f4da88209d5298e40867b08505661cfd451`.

**3 — Implementation paths present:** `core/models.py`; `src/intevia/core/contribution.py`; `src/intevia/services/contribution_authority.py`; `src/intevia/services/contribution_service.py`.

**4 — Test-definition paths present:** `tests/test_contribution_*.py` and related service tests. Presence is not execution.

**5 — Last recorded execution evidence:** Recorded execution is not restated here; inspect the linked S003 Datacron and its exact sources.

**6 — Slice-specific evidence source:** [`D-S003-GOVERNED-KNOWLEDGE-LINEAGE.md`](../holocron/datacrons/D-S003-GOVERNED-KNOWLEDGE-LINEAGE.md).

**7 — Independent reproduction:** Not established by this crosswalk.

**8 — Exact Human acceptance or closure source:** No acceptance or closure inference is made unless the linked source contains an exact Human-issued event.

**9 — Unresolved findings and deferrals:** No current-Human-acceptance inference is made by repository presence.

**10 — Deployment, release, certification, and external-validation state:** Internal pre-alpha. The [shared boundary](#shared-deployment-and-external-validation-boundary) applies; this block establishes no deployment, release, certification, or broad external-validation state.

**11 — Explicit non-claims:** No completeness, deployment, or universal workflow claim.

**12 — Last verification date:** 2026-07-31.

**13 — Staleness trigger:** Any change to the qualified ref, cited evidence, Human decision, or unresolved-finding state.

### 2. Identity, authentication, attendance, and direct self-registration

**1 — Capability family and bounded claim:** Paths are present for a bounded Identity foundation, authentication shell, attendance, personal event home, and direct self-registration.

**2 — Qualified repository ref:** `1aed0f4da88209d5298e40867b08505661cfd451`.

**3 — Implementation paths present:** `core/identity.py`; `core/models.py`; `core/forms.py`; `core/views.py`; `src/intevia/services/identity_service.py`; event attendance and self-registration services.

**4 — Test-definition paths present:** `tests/test_s007_*.py`; `tests/test_event_attendance_*.py`; `tests/test_s009_personal_event_home.py`; `tests/test_s010_direct_self_registration*.py`. Presence is not execution.

**5 — Last recorded execution evidence:** Historical recorded results are carried only by the relevant exact evidence sources; no current rerun is claimed.

**6 — Slice-specific evidence source:** [`D-S009-FIRST-HUMAN-LANDING-PAD.md`](../holocron/datacrons/D-S009-FIRST-HUMAN-LANDING-PAD.md) and [`D-S010-GOVERNED-DIRECT-SELF-REGISTRATION.md`](../holocron/datacrons/D-S010-GOVERNED-DIRECT-SELF-REGISTRATION.md).

**7 — Independent reproduction:** Not established by this crosswalk.

**8 — Exact Human acceptance or closure source:** No acceptance or closure inference is made unless the linked source contains an exact Human-issued event.

**9 — Unresolved findings and deferrals:** Broader account lifecycle, SSO, organisation administration, and production authentication are not established.

**10 — Deployment, release, certification, and external-validation state:** Internal pre-alpha. The [shared boundary](#shared-deployment-and-external-validation-boundary) applies; this block establishes no deployment, release, certification, or broad external-validation state.

**11 — Explicit non-claims:** No general identity-platform or operational-security claim.

**12 — Last verification date:** 2026-07-31.

**13 — Staleness trigger:** Any change to the qualified ref, cited evidence, Human decision, or unresolved-finding state.

### 3. Events and governed registration

**1 — Capability family and bounded claim:** Paths are present for Event lifecycle, governed registration, attendance, and read services.

**2 — Qualified repository ref:** `1aed0f4da88209d5298e40867b08505661cfd451`.

**3 — Implementation paths present:** `core/models.py`; `src/intevia/services/event_service.py`; registration, attendance, and read services under `src/intevia/services/`.

**4 — Test-definition paths present:** `tests/test_events_*.py`; `tests/test_event_registration_*.py`; `tests/test_event_attendance_*.py`. Presence is not execution.

**5 — Last recorded execution evidence:** Recorded execution is not converted into a current-pass claim.

**6 — Slice-specific evidence source:** [`D-S006-GOVERNED-EVENT-REGISTRATION-FOUNDATION.md`](../holocron/datacrons/D-S006-GOVERNED-EVENT-REGISTRATION-FOUNDATION.md).

**7 — Independent reproduction:** Not established by this crosswalk.

**8 — Exact Human acceptance or closure source:** No acceptance or closure inference is made unless the linked source contains an exact Human-issued event.

**9 — Unresolved findings and deferrals:** No full event-management, ticketing, scheduling, or deployment claim.

**10 — Deployment, release, certification, and external-validation state:** Internal pre-alpha. The [shared boundary](#shared-deployment-and-external-validation-boundary) applies; this block establishes no deployment, release, certification, or broad external-validation state.

**11 — Explicit non-claims:** No complete Events-module claim.

**12 — Last verification date:** 2026-07-31.

**13 — Staleness trigger:** Any change to the qualified ref, cited evidence, Human decision, or unresolved-finding state.

### 4. Library resources and exact-version binding

**1 — Capability family and bounded claim:** Paths are present for governed Library resources and exact-version contracts, policy, service, and PostgreSQL-oriented tests.

**2 — Qualified repository ref:** `1aed0f4da88209d5298e40867b08505661cfd451`.

**3 — Implementation paths present:** `src/intevia/services/library_service.py`; `library_exact_version_contract.py`; `library_exact_version_policy.py`; related models and migrations.

**4 — Test-definition paths present:** `tests/test_library_*.py`. Presence is not execution.

**5 — Last recorded execution evidence:** Inspect the linked S003 and S011-A records for bounded historical execution claims.

**6 — Slice-specific evidence source:** [`D-S003-GOVERNED-KNOWLEDGE-LINEAGE.md`](../holocron/datacrons/D-S003-GOVERNED-KNOWLEDGE-LINEAGE.md) and [`D-S011A-GOVERNED-LIBRARY-EXACT-VERSION-CONTRACT.md`](../holocron/datacrons/D-S011A-GOVERNED-LIBRARY-EXACT-VERSION-CONTRACT.md).

**7 — Independent reproduction:** Not established by this crosswalk.

**8 — Exact Human acceptance or closure source:** No acceptance or closure inference is made unless the linked source contains an exact Human-issued event.

**9 — Unresolved findings and deferrals:** No complete library product, discovery system, or external content platform.

**10 — Deployment, release, certification, and external-validation state:** Internal pre-alpha. The [shared boundary](#shared-deployment-and-external-validation-boundary) applies; this block establishes no deployment, release, certification, or broad external-validation state.

**11 — Explicit non-claims:** No operational completeness or current-pass inference.

**12 — Last verification date:** 2026-07-31.

**13 — Staleness trigger:** Any change to the qualified ref, cited evidence, Human decision, or unresolved-finding state.

### 5. Services, activity orchestration, and event-resource readback

**1 — Capability family and bounded claim:** Paths are present for governed Service foundations, activity assignment/delivery/readback, and Event-resource relationship contracts and readback.

**2 — Qualified repository ref:** `1aed0f4da88209d5298e40867b08505661cfd451`.

**3 — Implementation paths present:** Service, activity, authority, and Event-resource relationship modules under `src/intevia/services/`; related `core/models.py` and migrations.

**4 — Test-definition paths present:** `tests/test_service_*.py`; `tests/test_event_resource_relationship_*.py`. Presence is not execution.

**5 — Last recorded execution evidence:** Historical results remain scoped to the linked records and their named repository states.

**6 — Slice-specific evidence source:** [`D-S004-GOVERNED-SERVICE-FOUNDATION.md`](../holocron/datacrons/D-S004-GOVERNED-SERVICE-FOUNDATION.md), [`D-S011B-GOVERNED-EVENT-RESOURCE-LINKING-AND-READBACK.md`](../holocron/datacrons/D-S011B-GOVERNED-EVENT-RESOURCE-LINKING-AND-READBACK.md), and [`D-S012-GOVERNED-SERVICE-ACTIVITY-ASSIGNMENT-DELIVERY-AND-READBACK.md`](../holocron/datacrons/D-S012-GOVERNED-SERVICE-ACTIVITY-ASSIGNMENT-DELIVERY-AND-READBACK.md).

**7 — Independent reproduction:** Not established by this crosswalk.

**8 — Exact Human acceptance or closure source:** No acceptance or closure inference is made unless the linked source contains an exact Human-issued event.

**9 — Unresolved findings and deferrals:** No general workflow engine, marketplace, payment, or cross-organism capability.

**10 — Deployment, release, certification, and external-validation state:** Internal pre-alpha. The [shared boundary](#shared-deployment-and-external-validation-boundary) applies; this block establishes no deployment, release, certification, or broad external-validation state.

**11 — Explicit non-claims:** No complete Service-module or deployment claim.

**12 — Last verification date:** 2026-07-31.

**13 — Staleness trigger:** Any change to the qualified ref, cited evidence, Human decision, or unresolved-finding state.

### 6. Service-submission profile effect

**1 — Capability family and bounded claim:** Paths are present for bounded profile-effect authority, contract, service, and readback following governed service submission.

**2 — Qualified repository ref:** `1aed0f4da88209d5298e40867b08505661cfd451`.

**3 — Implementation paths present:** `src/intevia/services/profile_effect_authority.py`; `profile_effect_contract.py`; `profile_effect_service.py`; `profile_effect_read_service.py`; migration `0017`.

**4 — Test-definition paths present:** `tests/test_profile_effect_*.py`. Presence is not execution.

**5 — Last recorded execution evidence:** The S013 record is the route to bounded execution and reconciliation evidence; no current rerun is claimed.

**6 — Slice-specific evidence source:** [`D-S013-GOVERNED-SERVICE-SUBMISSION-PROFILE-EFFECT.md`](../holocron/datacrons/D-S013-GOVERNED-SERVICE-SUBMISSION-PROFILE-EFFECT.md).

**7 — Independent reproduction:** Not established by this crosswalk.

**8 — Exact Human acceptance or closure source:** No acceptance or closure inference is made unless the linked source contains an exact Human-issued event.

**9 — Unresolved findings and deferrals:** No general reputation, scoring, radiance, or recognition-system claim.

**10 — Deployment, release, certification, and external-validation state:** Internal pre-alpha. The [shared boundary](#shared-deployment-and-external-validation-boundary) applies; this block establishes no deployment, release, certification, or broad external-validation state.

**11 — Explicit non-claims:** No universal profile-effect semantics or operational readiness.

**12 — Last verification date:** 2026-07-31.

**13 — Staleness trigger:** Any change to the qualified ref, cited evidence, Human decision, or unresolved-finding state.

### 7. Education Course-definition foundation

**1 — Capability family and bounded claim:** Paths are present for a bounded governed Course aggregate, immutable versions, authority, create/append service, and creator-only readback.

**2 — Qualified repository ref:** `1aed0f4da88209d5298e40867b08505661cfd451`.

**3 — Implementation paths present:** `core/models.py`; migration `core/migrations/0018_s014_education_course_foundation.py`; four `education_course_*` service modules.

**4 — Test-definition paths present:** Seven `tests/test_education_course_*.py` paths plus the compatibility paths named in the S014 record. Presence is not execution.

**5 — Last recorded execution evidence:** The S014 Datacron records focused 50/50, PostgreSQL compatibility 49/49, and full PostgreSQL regression 663/663 at its named implementation state. The static baseline also contains 663 textual test-definition matches; these numerically equal values are different evidence classes and do not establish that every static match was the executed regression selection.

**6 — Slice-specific evidence source:** [`D-S014-GOVERNED-EDUCATION-COURSE-DEFINITION.md`](../holocron/datacrons/D-S014-GOVERNED-EDUCATION-COURSE-DEFINITION.md).

**7 — Independent reproduction:** Not established by this crosswalk.

**8 — Exact Human acceptance or closure source:** No acceptance or closure inference is made unless the linked source contains an exact Human-issued event.

**9 — Unresolved findings and deferrals:** `MAT-S014-01` remains deferred to IDOP v0.9.6. The record at this baseline says it is pending direct Human implementation acceptance, Datacron acceptance, and closure.

**10 — Deployment, release, certification, and external-validation state:** Internal pre-alpha. The [shared boundary](#shared-deployment-and-external-validation-boundary) applies; this block establishes no deployment, release, certification, or broad external-validation state.

**11 — Explicit non-claims:** No curriculum delivery, class delivery, enrolment, learning, completion, assessment, certification, educator qualification, payment, publication, or operational Course claim.

**12 — Last verification date:** 2026-07-31.

**13 — Staleness trigger:** Any change to the qualified ref, cited evidence, Human decision, or unresolved-finding state.

## Static inventory note

At the qualified ref, `git ls-files 'test_*.py' '**/test_*.py' | sort -u | wc -l` reports 84 tracked test-file paths. Applying `^\s*(async\s+)?def\s+test_` to tracked Python files reports 663 textual test-definition matches. These are reproducible static inventory methods, not collection or execution. The value 663 collides numerically with a recorded S014 regression result and is explicitly disambiguated above.

## Staleness and maintenance

This crosswalk becomes stale when the qualified ref changes, a cited evidence object is superseded, an exact Human decision changes a boundary, or an unresolved finding changes state. Datacron narratives should be linked rather than copied or silently reinterpreted.
