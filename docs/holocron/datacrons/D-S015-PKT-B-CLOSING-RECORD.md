# PKT-B — Closing record

Datacron closing record v1.0 · 1 November 2026

## In plain words

**PKT-B has delivered the organisation and office foundations described in its main record, together with the final office safeguards. The remaining work is identified and assigned to the packages that follow.** On 1 November 2026 the Human Governor closed PKT-B against this record and those transfers (§6).

The final safeguard unit, **SR-H**, strengthens the database's own protection of office records. It makes the database refuse prohibited changes even when code tries to write directly to it, bypassing the application's ordinary checks. These protections cover outdated office definitions, the founding Coordinator office, reserved INTEVIA offices, permanent board offices, changes to an office's type, and restarting a retired office without naming its latest version.

SR-H landed on 1 November. It was the **seventh consecutive unit to pass its first qualifying GitHub run**: offices C2, offices C3, database copies in the GitHub checks, offices C4, offices C5, offices C6 and SR-H. The Human Governor also accepted the plan for the work after PKT-B.

This is the closing account of the work; the closure itself is the Human Governor's decision (§6). It does not declare the website complete, close S015 or authorise public use.

## 1. What this record covers

This short record supplements **“PKT-B — Governed Organisations, Membership and Offices”**, version 1.0.2, which was added to the main codebase with SR chunk C6. It covers three developments since that account: SR-H, the accepted plan for the next packages, and the transfer of unfinished work.

PKT-B's delivered product work meets the boundary described in that earlier record, with SR-H's safeguards added. Section 4 identifies where the remaining work goes.

Here, **PKT-B** means packet B of **S015**, the organisation development slice: the same terms the main record uses. A **Datacron** is the project's account of what was delivered, the supporting evidence and its limits. The earlier Datacron is identified in the source table below.

## 2. SR-H: the final office safeguards

SR-H adds database protection that must be in place before users gain access to the office functions. The database now refuses attempts to:

- appoint someone using an outdated office definition;

- create a second Coordinator definition or deactivate the founding one;

- change a reserved INTEVIA office through an ordinary organisation's route;

- retire a board office or change an office's type;

- restart a retired office without identifying its latest version. This is the gap the Human Governor's Q2 decision closed (below): without it, a retired custom office could be restarted as a board office.

An **office definition** records what an office is. An appointment records who holds it. These are separate records.

SR-H also improves how the application reads a saved consent result. If that saved record is damaged or malformed, the application reports a record fault. It no longer describes this as the user sending a different request.

### Delivery and evidence

| Item | What the record shows |
| - | - |
| Database update | Migration `0053_sr_office_safeguards` rewrites the bodies of three existing database guard functions, keeping every existing check and trigger. A migration is a controlled update to the database. This one refuses to proceed, or to reverse itself, if the database does not match the state it expects |
| Other changes | Stricter reading of saved consent results; one helper function made explicitly available to other code; a plain-English walkthrough added to the Assessor Guide (§3a); one line of the local test runner updated to expect migration 0053 |
| Files | **11 files:** eight changed and three added |
| Product tests | **134 tests**, up from 126. Eight were added; two existing tests were updated for the new refusal messages, with no change to what they test |
| Deliberate-fault checks | The existing **19 checks** are unchanged. These deliberately introduce faults to establish that the tests detect them. Six additional, once-only admission checks are recorded separately and do not run in continuous integration (CI), the automated GitHub checks |
| Design, review and test admission | Designed by GPT Astra as acting Lead Designer (four versions). Advised by Claude Fable, including one corrected database rule (§5). SR-H's code was reviewed by Grok in two steps. Rehearsed by the Vision Chamber on PostgreSQL 17. The Human Governor signed the test admission report before the qualifying run |
| Local preparation | The first local run stopped before any test because the runner still expected migration 0052. After the one-line correction, the second run passed **134 of 134 tests** |
| Qualifying GitHub run | Run **41352656205**, pull request **#32**, commit `02790ab8`: **134 product tests passed**, and **19 of 19 deliberate-fault checks passed**; the run qualified as evidence. The verification route took **4 min 12 s**; the whole job took about **4 min 49 s** |
| Acceptance and landing | The Human Governor said, **“Accepted. Let's land it”**. The main codebase advanced from `f2002428` to `02790ab8` at **21:46:28 UTC on 1 November 2026** |


### The Human Governor's decisions

- **Q1, option A — keep the safeguard and disclose the missing test.** The reserved-office update safeguard was installed. There is not yet a lawful reserved-office record against which to exercise it. That test remains assigned to follow-up item **FW-11**.

- **Q2 — close the gap now.** A retired custom office could have been recreated directly as a board office, escaping the rule that its type must remain fixed. The Human Governor directed that this gap be closed in SR-H.

### Limits of the evidence

The reserved-office update safeguard still needs a test using a real, lawfully created record (FW-11). One refusal in the rule for restarting a retired office has no dedicated test of its own. Two once-only admission results retain disclosed limitations: one concerns how the test machinery labels a result; in the other, the check that keeps a fault run isolated stopped the route as designed. The PostgreSQL 17 rehearsal was preparation; the qualifying GitHub run remains the evidence of record.

## 3. The accepted plan for the next work

The Human Governor accepted **S015 re-plan v0.9** as the planning baseline on 1 November 2026. It considers the remaining work against three beta cases: **AI in the Home**, **HAT Practitioner Certification Curriculum (HPCC) Module 0**, and the **democracy experiments**.

During **alpha**, the Human Governor tests the core functions and complete journeys. During **beta**, real people use the agreed test cases.

The plan sets these directions:

- **Packages C, D and E.** C covers membership, authority and continuity when people leave or become unavailable. D covers private access and understandable history. E brings the evidence together and supports the decision to close S015.

- **The root organisation, Circles and collective decisions.** Package R covers INTEVIA's own organisation. The Circles package provides functioning groups within organisations. Circular Qualification (**CQ**) provides their collective decision process. The packages must be delivered in the order their dependencies require; Circles must be available for CQ to operate.

- **Different databases for alpha and beta.** Alpha proves the root organisation's machinery on a throwaway database. Beta begins the enduring record on a database retained into version 1.0 and beyond. Resetting that database is not a way to solve a migration problem.

- **Focused checks of the remaining domains.** Start with EVENT, then LIBRARY's requirements for organisational law and DISCUSSION's support for decision-making.

- **Test and time budgets.** The planning ceiling is **150 product tests**, with a **15-minute target for the total CI job**. The Human Governor must be told before proposed work would exceed either.

- **Proportionate database protection.** Use the simplest design that protects the stated requirement. Every proposed new database safeguard must explain the failure it prevents and why existing protection is insufficient.

This is an accepted direction for planning, not a delivery forecast. Each implementation unit still needs its own commission with a defined scope.

## 4. Where the remaining work goes

The table records both the work completed by SR-H and the work continuing elsewhere. **FW** numbers identify existing follow-up items; they are retained so each item can be traced to its earlier record.

| Work or remaining gap | Destination or status | Supporting record |
| - | - | - |
| Rejoining; leaving while holding an office; mentorship and probation; recovery when no Coordinator remains | **C1–C3**, supported by the recovery decision sheet | Re-plan P1, P3 |
| Database protection for offices (**FW-5**); safer reading of stored consent results (**FW-14**) | **Completed in SR-H**, with the reserved-office test still outstanding as described below | §2 |
| A test using a real, lawfully created reserved-office record (**FW-11**) | **Package R**, proved during alpha | Re-plan decision 9 |
| Custom-office behaviour when a subscription expires (**FW-6, FW-12**) | The unit covering subscription entitlements over time. **Custom offices remain unavailable to users until then** | Main PKT-B Datacron §9 |
| Required offices, removal under the constitution, re-establishment and observations by CARE (**FW-1, FW-1a, FW-3, FW-9**) | **LIBRARY's organisational-law work**, with CARE, INTEVIA's advisory and observation component | Re-plan P7 |
| Renaming standard offices (**FW-2**) | The **standard role types unit** | Main PKT-B Datacron §9 |
| Support tickets and accurate follow-up (**FW-4**) | The **Support ServiceFlow**, brought forward wherever other work needs it. This is the process for receiving and handling support requests | Re-plan P7 |
| The order in which Circle actions are refused (**FW-16, Circle note**); offices belonging to Circles | The **Circles package** | Re-plan decision 10 |
| The value of retained tests, overlapping coverage, archived failures and test-admission gaps A–C (**FW-13, FW-15, FW-16 archived note**) | The **test-value review** | Main PKT-B Datacron §9 |
| **FW-10's final point:** the function that turns database refusals into messages, `definition_guard_message`, was not explicitly available to other code | **Completed in SR-H.** The C6 return recorded this point as partial and named this function. SR-H adds it to the service's explicit export list | §2 |
| The Assessor Guide's GitHub entry, roadmap and implementation crosswalk | A **small documentation unit**. The crosswalk links the requirements to the code that implements them | Re-plan Part B |
| Approved explanations within the code, awaiting application (**docstring packets 51–61**) | One **Lead Designer-built application of the approved text**, before C1 changes those files | Re-plan §8 |


## 5. Problems found and corrected

The successful final run followed corrections made during preparation and review. Those remain part of the record.

- **The local runner expected the previous database version.** Earlier migration units had updated this one-line setting. SR-H's design and the Vision Chamber's release checks missed it because the rehearsal and CI determine the latest migration differently. Local run 1 stopped safely before testing began. The Human Governor approved the one-line correction as a recorded exception. Release checks now compare the runner's setting with the newest migration.

- **An Advisor correction also needed correcting.** The Advisor's first replacement wording for a database rule tried to read a value before assigning it. The Vision Chamber's rehearsal found the problem, with **11 failures and 15 errors**. The Advisor acknowledged the mistake. The corrected rule was checked byte for byte before release.

- **A review handover named the wrong reviewer.** The material first sent to the new Reviewer retained the previous Reviewer's name. The source material was identical, so this did not invalidate the review evidence.

- **A run report misstated what happened.** The Making Engine's report for run 1 said that no database had been created. The execution records showed that a temporary database had been created and then removed during recovery. Run 2's report corrected the account.

A clean final run does not erase these earlier errors.

## 6. Decisions that remain separate

Under the standing ruling, the Human Governor accepts this record by authorising its commit to the repository.

**PKT-B is closed.** On 1 November 2026 the Human Governor closed S015 PKT-B against this closing record and its explicit transfers. The unfinished work continues in its named destinations (§4).

Neither acceptance of this record nor the closure of PKT-B makes any of these three further decisions:

- **Close S015**, when its agreed scope has been delivered.

- **Sign off whole-product alpha.**

- **Authorise beta.**

Each remains a separate act of the Human Governor.

Final authority remains with **Carmian Owen, Human Governor**.

## Sources

The shortened digests identify the cited records. The Vision Chamber checked each against the staged file.

| Source | Identity |
| - | - |
| Landed PKT-B Datacron | `docs/holocron/datacrons/D-S015-PKT-B-GOVERNED-ORGANISATIONS-MEMBERSHIP-AND-OFFICES.md` at `f2002428` |
| SR-H acceptance and landing | `HG_ACCEPTANCE_SR_H_AND_LANDING_20261101_CAPTURE.txt` (f6e3d8cb…9033); `SR_H_LANDING_RECORD.txt` (8071dae3…5035) |
| SR-H CI | `VC_CHECK_CI_SR_H_RUN1_v1_0.md` (c1bbb6f7…90d7); run 41352656205 evidence artifact |
| FW-10 | C6 return v1.0 §FW-10, point 6; `src/intevia/services/organism_role_definition_service.py` export list at `02790ab8` |
| SR-H decisions | `HG_DECISIONS_SR_H_Q1_Q2_20261101_CAPTURE.txt` (35661564…8079); `HG_EXCEPTION_SR_H_RUNNER_HEAD_20261101_CAPTURE.txt` (e63d127c…bf80); `HG_TAR_SIGNOFF_SR_H_20261101_CAPTURE.txt` (6abaed57…c292) |
| Migration 0053 | `core/migrations/0053_sr_office_safeguards.py` at `02790ab8`, module description l.3 ("Replace three guard bodies, keeping every existing branch and trigger binding"); commit message of `02790ab8` |
| §5 corrections | `VC_CHECK_ASTRA_SR_H_v1_1.md` (24f257e4…e890): the Advisor rule's rehearsal, 11 failures and 15 errors. `VC_CLAIM_CHECK_GROK_SR_H_STEP1_v1_0.md` (5258b1c4…db5) and `HG_DECISION_SR_H_REVIEWER_GROK_FOR_ARGON_20261101_CAPTURE.txt` (615bebf3…40f3a): the review set relayed under the previous Reviewer's name. `VC_CHECK_ME_SR_H_RUN1_v1_1.md` and `VC_CHECK_ME_SR_H_RUN2_v1_0.md`: the run 1 report correction |
| PKT-B closure | `HG_DECISION_CLOSE_S015_PKT_B_20261101_CAPTURE.txt` (470850ee…ee7c) |
| Re-plan | `S015_REPLAN_PKT_C_D_E_v0_9.md` (67610cc5…99d4); acceptance capture (c2fc112d…42ad) |


