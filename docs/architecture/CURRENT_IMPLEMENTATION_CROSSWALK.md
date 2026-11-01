# INTEVIA v1.0 — Current Implementation Crosswalk

Qualified repository ref: `02790ab8572181ba602ec1404eb8da90666c1f5f` (document header; each row keeps the ref at which its paths were checked)  
Last verified: 2026-11-01 (documentation and path review)  
Document role: derivative evidence-state navigation  
Authority effect: none

For the current position and its limits, see [the README](../../README.md#current-position).

The current development practice is **IDOPT (formerly IDOP)** — INTEVIA Development Operating Protocol and Thinking. Historical records and protocol filenames retain IDOP.

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

## Current testing boundary

GitHub continuous integration (CI) is the evidence of record. At SR-H the route recorded 134 product tests and 19 deliberate-defect checks under the six test admission rules (TAR). The old suite is archived and not run; selective reuse must satisfy the rules. The route uses one prepared database template and disposable copies. See [verification](../verification/README.md) and [the archive record](../../archive/tests_2026_10/00_ARCHIVE_RECORD.md).

Fields 3 and 4 below are current path checks. Older execution figures remain historical at their named versions. Presence in the archive supplies no current test coverage. A glob such as `archive/tests_2026_10/test_contribution_*.py` means the matching files in that directory, not a new test selection.

## Capability blocks

### 1. Contribution and governed knowledge lineage

**1 — Capability family and bounded claim:** Paths are present for contribution lifecycle, authority, correction, privacy, archive, and service behaviour.

**2 — Qualified repository ref:** `f829d1b2b714cb282d96a273fbd0887eac9c3522`.

**3 — Implementation paths present:** `core/models.py`; `src/intevia/core/contribution.py`; `src/intevia/services/contribution_authority.py`; `src/intevia/services/contribution_service.py`.

**4 — Test-definition paths present:** `archive/tests_2026_10/test_contribution_*.py` and related service tests. Archived test files are not run by the current route. Presence is not execution.

**5 — Last recorded execution evidence:** Recorded execution is not restated here; inspect the linked S003 Datacron and its exact sources.

**6 — Slice-specific evidence source:** [`D-S003-GOVERNED-KNOWLEDGE-LINEAGE.md`](../holocron/datacrons/D-S003-GOVERNED-KNOWLEDGE-LINEAGE.md).

**7 — Independent reproduction:** Not established by this crosswalk.

**8 — Exact Human acceptance or closure source:** No acceptance or closure inference is made unless the linked source contains an exact Human-issued event.

**9 — Unresolved findings and deferrals:** No current-Human-acceptance inference is made by repository presence.

**10 — Deployment, release, certification, and external-validation state:** Internal pre-alpha. The [shared boundary](#shared-deployment-and-external-validation-boundary) applies; this block establishes no deployment, release, certification, or broad external-validation state.

**11 — Explicit non-claims:** No completeness, deployment, or universal workflow claim.

**12 — Last verification date:** 2026-11-01 for file locations only; recorded run dates remain those of their cited evidence.

**13 — Staleness trigger:** Any change to the qualified ref, cited evidence, Human decision, or unresolved-finding state.

### 2. Identity, authentication, attendance, and direct self-registration

**1 — Capability family and bounded claim:** Paths are present for a bounded Identity foundation, authentication shell, attendance, personal event home, and direct self-registration.

**2 — Qualified repository ref:** `f829d1b2b714cb282d96a273fbd0887eac9c3522`.

**3 — Implementation paths present:** `core/identity.py`; `core/models.py`; `core/forms.py`; `core/views.py`; `src/intevia/services/identity_service.py`; `src/intevia/services/event_attendance_service.py`; `src/intevia/services/event_self_registration_service.py`.

**4 — Test-definition paths present:** `archive/tests_2026_10/test_s007_*.py`; `archive/tests_2026_10/test_event_attendance_*.py`; `archive/tests_2026_10/test_s009_personal_event_home.py`; `archive/tests_2026_10/test_s010_direct_self_registration*.py`. Archived test files are not run by the current route. Presence is not execution.

**5 — Last recorded execution evidence:** Historical recorded results are carried only by the relevant exact evidence sources; no current rerun is claimed.

**6 — Slice-specific evidence source:** [`D-S009-FIRST-HUMAN-LANDING-PAD.md`](../holocron/datacrons/D-S009-FIRST-HUMAN-LANDING-PAD.md) and [`D-S010-GOVERNED-DIRECT-SELF-REGISTRATION.md`](../holocron/datacrons/D-S010-GOVERNED-DIRECT-SELF-REGISTRATION.md).

**7 — Independent reproduction:** Not established by this crosswalk.

**8 — Exact Human acceptance or closure source:** No acceptance or closure inference is made unless the linked source contains an exact Human-issued event.

**9 — Unresolved findings and deferrals:** Broader account lifecycle, SSO, organisation administration, and production authentication are not established.

**10 — Deployment, release, certification, and external-validation state:** Internal pre-alpha. The [shared boundary](#shared-deployment-and-external-validation-boundary) applies; this block establishes no deployment, release, certification, or broad external-validation state.

**11 — Explicit non-claims:** No general identity-platform or operational-security claim.

**12 — Last verification date:** 2026-11-01 for file locations only; recorded run dates remain those of their cited evidence.

**13 — Staleness trigger:** Any change to the qualified ref, cited evidence, Human decision, or unresolved-finding state.

### 3. Events and governed registration

**1 — Capability family and bounded claim:** Paths are present for Event lifecycle, governed registration, attendance, and read services.

**2 — Qualified repository ref:** `f829d1b2b714cb282d96a273fbd0887eac9c3522`.

**3 — Implementation paths present:** `core/models.py`; `src/intevia/services/event_service.py`; `src/intevia/services/event_registration_service.py`; `src/intevia/services/event_attendance_service.py`; `src/intevia/services/event_read_service.py`.

**4 — Test-definition paths present:** `archive/tests_2026_10/test_events_*.py`; `archive/tests_2026_10/test_event_registration_*.py`; `archive/tests_2026_10/test_event_attendance_*.py`. Archived test files are not run by the current route. Presence is not execution.

**5 — Last recorded execution evidence:** Recorded execution is not converted into a current-pass claim.

**6 — Slice-specific evidence source:** [`D-S006-GOVERNED-EVENT-REGISTRATION-FOUNDATION.md`](../holocron/datacrons/D-S006-GOVERNED-EVENT-REGISTRATION-FOUNDATION.md).

**7 — Independent reproduction:** Not established by this crosswalk.

**8 — Exact Human acceptance or closure source:** No acceptance or closure inference is made unless the linked source contains an exact Human-issued event.

**9 — Unresolved findings and deferrals:** No full event-management, ticketing, scheduling, or deployment claim.

**10 — Deployment, release, certification, and external-validation state:** Internal pre-alpha. The [shared boundary](#shared-deployment-and-external-validation-boundary) applies; this block establishes no deployment, release, certification, or broad external-validation state.

**11 — Explicit non-claims:** No complete Events-module claim.

**12 — Last verification date:** 2026-11-01 for file locations only; recorded run dates remain those of their cited evidence.

**13 — Staleness trigger:** Any change to the qualified ref, cited evidence, Human decision, or unresolved-finding state.

### 4. Library resources and exact-version binding

**1 — Capability family and bounded claim:** Paths are present for governed Library resources and exact-version contracts, policy, service, and PostgreSQL-oriented tests.

**2 — Qualified repository ref:** `f829d1b2b714cb282d96a273fbd0887eac9c3522`.

**3 — Implementation paths present:** `src/intevia/services/library_service.py`; `src/intevia/services/library_exact_version_contract.py`; `src/intevia/services/library_exact_version_policy.py`; related models and migrations.

**4 — Test-definition paths present:** `archive/tests_2026_10/test_library_*.py`. Archived test files are not run by the current route. Presence is not execution.

**5 — Last recorded execution evidence:** Inspect the linked S003 and S011-A records for bounded historical execution claims.

**6 — Slice-specific evidence source:** [`D-S003-GOVERNED-KNOWLEDGE-LINEAGE.md`](../holocron/datacrons/D-S003-GOVERNED-KNOWLEDGE-LINEAGE.md) and [`D-S011A-GOVERNED-LIBRARY-EXACT-VERSION-CONTRACT.md`](../holocron/datacrons/D-S011A-GOVERNED-LIBRARY-EXACT-VERSION-CONTRACT.md).

**7 — Independent reproduction:** Not established by this crosswalk.

**8 — Exact Human acceptance or closure source:** No acceptance or closure inference is made unless the linked source contains an exact Human-issued event.

**9 — Unresolved findings and deferrals:** No complete library product, discovery system, or external content platform.

**10 — Deployment, release, certification, and external-validation state:** Internal pre-alpha. The [shared boundary](#shared-deployment-and-external-validation-boundary) applies; this block establishes no deployment, release, certification, or broad external-validation state.

**11 — Explicit non-claims:** No operational completeness or current-pass inference.

**12 — Last verification date:** 2026-11-01 for file locations only; recorded run dates remain those of their cited evidence.

**13 — Staleness trigger:** Any change to the qualified ref, cited evidence, Human decision, or unresolved-finding state.

### 5. Services, activity orchestration, and event-resource readback

**1 — Capability family and bounded claim:** Paths are present for governed Service foundations, activity assignment/delivery/readback, and Event-resource relationship contracts and readback.

**2 — Qualified repository ref:** `f829d1b2b714cb282d96a273fbd0887eac9c3522`.

**3 — Implementation paths present:** `src/intevia/services/service_service.py`; `src/intevia/services/service_authority.py`; `src/intevia/services/service_activity_service.py`; `src/intevia/services/service_activity_read_service.py`; `src/intevia/services/event_resource_relationship_contract.py`; `src/intevia/services/event_resource_relationship_policy.py`; `src/intevia/services/event_resource_relationship_service.py`; `src/intevia/services/event_resource_relationship_read_service.py`; `core/models.py`.

**4 — Test-definition paths present:** `archive/tests_2026_10/test_service_*.py`; `archive/tests_2026_10/test_event_resource_relationship_*.py`. Archived test files are not run by the current route. Presence is not execution.

**5 — Last recorded execution evidence:** Historical results remain scoped to the linked records and their named repository states.

**6 — Slice-specific evidence source:** [`D-S004-GOVERNED-SERVICE-FOUNDATION.md`](../holocron/datacrons/D-S004-GOVERNED-SERVICE-FOUNDATION.md), [`D-S011B-GOVERNED-EVENT-RESOURCE-LINKING-AND-READBACK.md`](../holocron/datacrons/D-S011B-GOVERNED-EVENT-RESOURCE-LINKING-AND-READBACK.md), and [`D-S012-GOVERNED-SERVICE-ACTIVITY-ASSIGNMENT-DELIVERY-AND-READBACK.md`](../holocron/datacrons/D-S012-GOVERNED-SERVICE-ACTIVITY-ASSIGNMENT-DELIVERY-AND-READBACK.md).

**7 — Independent reproduction:** Not established by this crosswalk.

**8 — Exact Human acceptance or closure source:** No acceptance or closure inference is made unless the linked source contains an exact Human-issued event.

**9 — Unresolved findings and deferrals:** No general workflow engine, marketplace, payment, or cross-organism capability.

**10 — Deployment, release, certification, and external-validation state:** Internal pre-alpha. The [shared boundary](#shared-deployment-and-external-validation-boundary) applies; this block establishes no deployment, release, certification, or broad external-validation state.

**11 — Explicit non-claims:** No complete Service-module or deployment claim.

**12 — Last verification date:** 2026-11-01 for file locations only; recorded run dates remain those of their cited evidence.

**13 — Staleness trigger:** Any change to the qualified ref, cited evidence, Human decision, or unresolved-finding state.

### 6. Service-submission profile effect

**1 — Capability family and bounded claim:** Paths are present for bounded profile-effect authority, contract, service, and readback following governed service submission.

**2 — Qualified repository ref:** `f829d1b2b714cb282d96a273fbd0887eac9c3522`.

**3 — Implementation paths present:** `src/intevia/services/profile_effect_authority.py`; `src/intevia/services/profile_effect_contract.py`; `src/intevia/services/profile_effect_service.py`; `src/intevia/services/profile_effect_read_service.py`; migration `core/migrations/0017_s013_profile_effect.py`.

**4 — Test-definition paths present:** `archive/tests_2026_10/test_profile_effect_*.py`. Archived test files are not run by the current route. Presence is not execution.

**5 — Last recorded execution evidence:** The S013 record is the route to bounded execution and reconciliation evidence; no current rerun is claimed.

**6 — Slice-specific evidence source:** [`D-S013-GOVERNED-SERVICE-SUBMISSION-PROFILE-EFFECT.md`](../holocron/datacrons/D-S013-GOVERNED-SERVICE-SUBMISSION-PROFILE-EFFECT.md).

**7 — Independent reproduction:** Not established by this crosswalk.

**8 — Exact Human acceptance or closure source:** No acceptance or closure inference is made unless the linked source contains an exact Human-issued event.

**9 — Unresolved findings and deferrals:** No general reputation, scoring, radiance, or recognition-system claim.

**10 — Deployment, release, certification, and external-validation state:** Internal pre-alpha. The [shared boundary](#shared-deployment-and-external-validation-boundary) applies; this block establishes no deployment, release, certification, or broad external-validation state.

**11 — Explicit non-claims:** No universal profile-effect semantics or operational readiness.

**12 — Last verification date:** 2026-11-01 for file locations only; recorded run dates remain those of their cited evidence.

**13 — Staleness trigger:** Any change to the qualified ref, cited evidence, Human decision, or unresolved-finding state.

### 7. Education Course-definition foundation

**1 — Capability family and bounded claim:** Paths are present for a bounded governed Course aggregate, immutable versions, authority, create/append service, and creator-only readback.

**2 — Qualified repository ref:** `f829d1b2b714cb282d96a273fbd0887eac9c3522`.

**3 — Implementation paths present:** `core/models.py`; migration `core/migrations/0018_s014_education_course_foundation.py`; `src/intevia/services/education_course_authority.py`; `src/intevia/services/education_course_contract.py`; `src/intevia/services/education_course_service.py`; `src/intevia/services/education_course_read_service.py`.

**4 — Test-definition paths present:** Seven archived `archive/tests_2026_10/test_education_course_*.py` paths; the S014 record retains its historical compatibility references. Archived test files are not run by the current route. Presence is not execution.

**5 — Last recorded execution evidence:** The S014 Datacron records focused 50/50, PostgreSQL compatibility 49/49, and full PostgreSQL regression 663/663 at its named implementation state. The September static baseline also contained 663 textual test-definition matches; these numerically equal values are different evidence classes and do not establish that every static match was the executed regression selection.

**6 — Slice-specific evidence source:** [`D-S014-GOVERNED-EDUCATION-COURSE-DEFINITION.md`](../holocron/datacrons/D-S014-GOVERNED-EDUCATION-COURSE-DEFINITION.md).

**7 — Independent reproduction:** Not established by this crosswalk.

**8 — Exact Human acceptance or closure source:** No acceptance or closure inference is made unless the linked source contains an exact Human-issued event.

**9 — Unresolved findings and deferrals:** The S014 historical record defers `MAT-S014-01` to IDOP v0.9.6 and records implementation acceptance, Datacron acceptance and closure as pending at issue. This update does not change that historical record or infer a later closure.

**10 — Deployment, release, certification, and external-validation state:** Internal pre-alpha. The [shared boundary](#shared-deployment-and-external-validation-boundary) applies; this block establishes no deployment, release, certification, or broad external-validation state.

**11 — Explicit non-claims:** No curriculum delivery, class delivery, enrolment, learning, completion, assessment, certification, educator qualification, payment, publication, or operational Course claim.

**12 — Last verification date:** 2026-11-01 for file locations only; recorded run dates remain those of their cited evidence.

**13 — Staleness trigger:** Any change to the qualified ref, cited evidence, Human decision, or unresolved-finding state.

### 8. Living Organism substrate and the INTEVIA Lineage-Chain (Slice S015, packets PKT-A-2 and PKT-A-3)

**1 — Capability family and bounded claim:** Paths are present for organism, circle and membership anchors; twelve append-only governed event chains with a canonical byte form, per-row fingerprints and a bitemporal fold (PKT-A-2); and the INTEVIA Lineage-Chain (ILC) contract laid onto those chains — actor states, parts and commitments, an identity-resolution table with an append-only severance ledger (PKT-A-3). Their own records state designated, implemented and landed, not accepted, at issue. This update does not alter those records. PKT-B progress is in block 9; S015 is not closed here.

**2 — Qualified repository ref:** `f829d1b2b714cb282d96a273fbd0887eac9c3522`.

**3 — Implementation paths present:** `core/models.py`; `core/migrations/0019_s015_living_organism_foundation.py`; `core/migrations/0020_s015_genesis_identity_completeness.py`; `core/migrations/0021_s015_recorded_chain_and_bitemporal_fold.py`; `core/migrations/0022_s015_governed_chain_contract.py`; `core/organism_contract.py`; `src/intevia/services/organism_authority.py`; `src/intevia/services/organism_membership_service.py`; `vectors/S015_CANONICAL_FORM_s015f3_RULE_v0_1.md` and `vectors/s015f3_fixed_vectors.json`. The September review identified missing application-model representations of the 0022 tables. That is retained as a historical finding; this path review does not adjudicate its current closure.

**4 — Test-definition paths present:** Current selected foundation files: `tests/s015_0021_support.py`; `tests/test_s015_0021_negative_direct_sql.py`; `tests/test_s015_0022_contract.py`. The old positive controls and guardian tests are now `archive/tests_2026_10/test_s015_0021_positive_controls.py` and `archive/tests_2026_10/test_s015_postgresql_guardians.py`, not run by CI. The PKT-A-3 Datacron's warning about the old contract test describes its issue-time state; September corrections and current paths are distinguished in the [public crosswalk](../S015_PUBLIC_CROSSWALK.md).

**5 — Last recorded execution evidence:** The PKT-A-2 Datacron records 120/120 (78 negative, 34 positive, 8 guardian) on a fresh PostgreSQL 17.10 throwaway at implementation commit `859960d8`, and 163 triggers against a baseline of 83. The PKT-A-3 Datacron records gates 2–7 passed at Python 3.12.10 / PostgreSQL 17.10 against the verified candidate, every behavioural scenario as the non-superuser production role, with verification instruments held outside the repository. Both are recorded results at their named states; neither is a current-pass claim, and neither has been independently reproduced.

**6 — Slice-specific evidence source:** [`D-S015-PKT-A-2-GOVERNED-RECORDED-CHAIN-AND-BITEMPORAL-FOLD.md`](../holocron/datacrons/D-S015-PKT-A-2-GOVERNED-RECORDED-CHAIN-AND-BITEMPORAL-FOLD.md) and [`D-S015-PKT-A-3-INTEVIA-LINEAGE-CHAIN-ILC.md`](../holocron/datacrons/D-S015-PKT-A-3-INTEVIA-LINEAGE-CHAIN-ILC.md) (v0.8, a corrected record whose corrections are marked and dated). Current continuation: [Governed Organisations, Membership and Offices](../holocron/datacrons/D-S015-PKT-B-GOVERNED-ORGANISATIONS-MEMBERSHIP-AND-OFFICES.md).

**7 — Independent reproduction:** Not established by this crosswalk. An external byte-level read of the public repository on 2026-09-15 is recorded in the PKT-A-3 Datacron as the source of its corrections; it executed no tests.

**8 — Exact Human acceptance or closure source:** None. Implementation acceptance, Datacron acceptance and packet closure for both packets were recorded in their issue-time status blocks as not issued. PKT-A acceptance is not on the path forward, by Human ruling recorded in the PKT-A-2 Datacron.

**9 — Unresolved findings and deferrals:** The PKT-A-3 record is the source for September findings `U-14`, `BD-1`, `U-6`, `F-U21l-04`, the identity split, test contradictions, schema evolution and its design questions. They are not all restated here as present-day absence claims. PKT-B has delivered organisation actions and corrected tests since then. The current selected contract test still checks refusal of second-layer commitments while U-14 stands. This documentation pass makes no blanket closure claim; remaining foundation-finding reconciliation belongs with the Vision Chamber's ledger. Current office restrictions are in block 9 and the PKT-B Datacron.

**10 — Deployment, release, certification, and external-validation state:** Internal pre-alpha. The [shared boundary](#shared-deployment-and-external-validation-boundary) applies; this block establishes no deployment, release, certification, or broad external-validation state. The September record stated that no Organism or Circle was populated; that describes the historical state, not the organisation actions now recorded in PKT-B.

**11 — Explicit non-claims:** No operational organisation, completed second-layer commitment verification, complete ILC or general lineage-platform claim. Historical actor limitations must not be used to deny the organisation actions now recorded in PKT-B.

**12 — Last verification date:** 2026-11-01 for file locations only; recorded run dates remain those of their cited evidence.

**13 — Staleness trigger:** Any change to the qualified ref, cited evidence, Human decision, or unresolved-finding state — including the disposition of the items at field 9 in PKT-B.

### 9. PKT-B organisations, membership and offices

**1 — Capability family and bounded claim:** Recorded organisation actions through offices C5: membership, applications, founding, Birthing, governance, subscription feature access and office actions. No complete website journey or public-use approval is established.

**2 — Qualified repository ref:** `f829d1b2b714cb282d96a273fbd0887eac9c3522`.

**3–4 — Current implementation and related test paths:** All paths are relative to the repository root. Each row names its own coverage limit.

| Capability | Current implementation paths | Current related test files | Limit |
|---|---|---|---|
| Membership and final application outcomes | `src/intevia/services/organism_membership_service.py`; `src/intevia/services/organism_application_service.py`; `src/intevia/services/organism_application_authority.py`; `src/intevia/services/organism_application_reads.py` | `tests/test_seed_o24_application_terminal_finality.py` | The focused tests demonstrate final-outcome protection; they do not prove every membership or application obligation. |
| Founding, registration and explicit Birthing | `src/intevia/services/organism_formation_service.py`; `src/intevia/services/organism_formation_authority.py` | `tests/test_sr_definitions.py` | Behind-the-scenes actions, not a complete website. The cited file includes the founding regression, not full coverage of every formation action. |
| Governance formation and participation | `src/intevia/services/organism_governance_service.py`; `src/intevia/services/organism_application_authority.py` | `tests/test_sr_provider.py`; `tests/test_sr_office_actions.py` | Circle-specific office extensions and some participation-test evidence remain follow-up work. |
| Office definitions, nomination, consent and appointment | `src/intevia/services/organism_role_definition_service.py`; `src/intevia/services/organism_office_consent_service.py`; `src/intevia/services/organism_role_service.py`; `core/migrations/0051_sr_role_definitions.py`; `core/migrations/0052_sr_office_guard_widening.py` | `tests/test_sr_definitions.py`; `tests/test_sr_office_guards.py`; `tests/test_sr_provider.py`; `tests/test_sr_office_actions.py` | Custom offices remain held. SR-H adds the outstanding database protections before office user access. |
| Recorded requests and safe repeat handling | `src/intevia/services/command_intake.py`; `src/intevia/services/organism_command_support.py`; `core/migrations/0049_h100_command_intake.py` | `tests/test_sr_definitions.py`; `tests/test_sr_office_actions.py` | The selected office checks do not establish every command route. Stored consent-result hardening remains due. |
| Subscription-based feature access and complimentary beta access | `src/intevia/services/organism_entitlement_service.py`; `core/migrations/0046_se_entitlement_foundation.py`; `core/migrations/0047_sek1_platform_rule_statement_start.py`; `core/migrations/0048_se_entitlement_granted_event_guard.py` | No dedicated current witness claimed here | Feature access is not authority to act. The custom-office subscription-expiry journey still needs assurance. |

**5 — Last recorded execution evidence:** SR-H GitHub run [41352656205](https://github.com/alchemyofacceptance/INTEVIA/actions/runs/41352656205), 134 product tests plus 19 deliberate-defect checks. No rerun is claimed by this document.

**6 — Packet evidence source:** [Governed Organisations, Membership and Offices](../holocron/datacrons/D-S015-PKT-B-GOVERNED-ORGANISATIONS-MEMBERSHIP-AND-OFFICES.md), with its [closing record](../holocron/datacrons/D-S015-PKT-B-CLOSING-RECORD.md).

**7 — Independent reproduction:** Not performed for this documentation update.

**8 — Human authority:** The Datacron records the Human Governor's decisions. Under the 19 October ruling, his committing or authorising commitment accepts that record, not unfinished work.

**9 — Unresolved work:** The SR-H database safeguards and the stored consent-result hardening landed at `02790ab8`; the reserved-office update safeguard awaits its physical test (FW-11). Custom offices are held pending subscription assurance; later Circle, law-in-force, naming, support and test-value work remain. See Datacron §9 and the [closing record](../holocron/datacrons/D-S015-PKT-B-CLOSING-RECORD.md) §4 for assigned homes.

**10 — Deployment and release:** Pre-alpha; the shared boundary applies.

**11 — Non-claims:** No complete website, universal test coverage or release readiness.

**12 — Last verification date:** 2026-11-01, documentation and path checks only.

**13 — Staleness trigger:** A changed base, run, Human decision or forward-work disposition.

## Static inventory note — September history

At the earlier September ref `4282ebcbc6c3b942c0b6c5efa8b3ff1a01ad73e0`, `git ls-files 'test_*.py' '**/test_*.py' | sort -u | wc -l` reports 97 tracked paths. Because a git pathspec `*` matches across directory separators, that figure includes three files matched through the directory name `intevia/test_postgresql_backend/`; 94 of the 97 are files whose own name begins `test_` (92 under `tests/`, plus the former core contract-test location and `intevia/test_settings.py`). Applying `^\s*(async\s+)?def\s+test_` to tracked Python files reports 805 textual test-definition matches. These are reproducible static inventory methods, not collection or execution, and neither figure is an executed-selection count.

The current census is in [PKT-B §2](../holocron/datacrons/D-S015-PKT-B-GOVERNED-ORGANISATIONS-MEMBERSHIP-AND-OFFICES.md). It distinguishes the archive from active tests and measures volume, not coverage.

A full line census of the repository at implementation commit `5eb7783`, by category, with per-file identities and the script that produced it, is carried in the PKT-A-3 Datacron at §2 and will be carried by each subsequent Datacron at its own landing commit.

## Staleness and maintenance

This crosswalk becomes stale when the qualified ref changes, a cited evidence object is superseded, an exact Human decision changes a boundary, or an unresolved finding changes state. Datacron narratives should be linked rather than copied or silently reinterpreted.
