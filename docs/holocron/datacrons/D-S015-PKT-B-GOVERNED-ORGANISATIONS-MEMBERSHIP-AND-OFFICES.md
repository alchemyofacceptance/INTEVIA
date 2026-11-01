# PKT-B — Governed Organisations, Membership and Offices

Datacron v1.0.2 · 1 November 2026 · written at SR C6, before its landing

A Datacron is a lasting account of what was built, why it changed and what remains owed. PKT-B means packet B of INTEVIA's organisation and recorded-authority work. This account reaches the completed offices step C5. It was written at step C6, which documents that work; C6 changes no product behaviour.

## In plain words

This packet built foundations for people to take part in an organisation safely and on the record. It covers joining, applying, founding an organisation, formally bringing it into being through **Birthing**, establishing governance, and holding an office. The records distinguish membership from office, a nomination from consent, and consent from an authorised appointment. They preserve who acted and on what authority.

The work is not yet a complete website experience or permission to open these features to users. Further safeguards and checks remain due. [Sources: B-15 through B-19 §1; B-36 §§1, 5; C6 dispositions.]

The way the team built and checked the product also changed. Repairing the old test suite had become unsustainable. The Human Governor chose to set it aside and rebuild around the obligations the product must honour. He later identified another saving: reuse prepared database copies during automatic checks instead of repeatedly rebuilding them. Independent review and corrections came before the final acceptance runs. [Sources: B-35 §1; pilot additions B35 v0.4; B36 v0.1–v0.2.]

The record shows a substantial turnaround:

- **145 checks under the six test admission rules**, against about **1,800 tests** before. The current total is 126 product tests and 19 deliberate-defect checks. The latter show whether selected tests notice something going wrong.

- The automatic checking sequence on GitHub now takes **under 4 minutes**, against **46 min 37 s** before. At C5 it took 3 min 32 s.

- **Five consecutive final candidates passed their first qualifying GitHub run**: offices C2, offices C3, the database-copy improvement, offices C4 and offices C5.

- **Offices C2 to C5 reached the shared product in two days**, 31 October and 1 November. C5 took **3 h 04 min 11 s** from commission to its qualifying run.

Limits: the smaller suite is not proven to give equivalent coverage; some test-rule evidence remains open; C5's whole GitHub job took 4 min 10 s; first-time passes followed design corrections. [Sources: B-35 §1; B-36 §§0–2; pilot additions B36 v0.2; CI growth table v1.3; practice item 159.]

The Human Governor wrote: **“Time and quality gains at the same time are always a delight and a sign of healthy evolution for me.”** That records his assessment and hope. Longer-term defects, maintenance cost and test growth still need measurement. [Pilot additions B36 v0.2.] Please note that the pilot is ongoing, and its results will be reported at a later date.

## 1. Status

PKT-B delivered foundations for recorded membership, applications, founding, governance and offices. It also delivered a much smaller, faster checking process. The product can now do substantial work behind the scenes, within the boundaries described in §4 and §7.

Limit: this is not the whole organisation experience, closure of the packet, or approval for public use.

This record describes the product as it stood when offices C5 reached the shared version. Earlier work that existed only on a separate development version is identified in the timeline. A precisely identified saved version is called a *commit*. [Sources: commission B2.7; B-30 §1; B-36 §§1–5; base commit `f829d1b2b714cb282d96a273fbd0887eac9c3522`.]

The record will be checked against its sources and independently reviewed. Under the Human Governor's 19 October ruling, committing the Datacron, or authorising that commitment, constitutes his acceptance. No separate acceptance ceremony is required.

Limit: accepting the record does not accept unfinished product work. [Sources: commission B2.7; B-30 §1.]

## 2. Identity

The work joins two product areas: CORE, the shared foundations, and ORGANISM, the organisation area. A **Living Organism** is INTEVIA's term for an organisation. The records keep four things separate: membership, what an office is, who is nominated, and who is appointed.

The account draws on 36 unit records. These comprise the early exploration record B-0, records B-1 to B-24, and B-26 to B-36. **There was no B-25 channel; its number was skipped.** The Human Governor confirmed this on 1 November. B-37 commissioned this catch-up. The source files use `US015` to identify this development stream. [Sources: commission B1; `02_UNIT_RECORDS/`.]

Limit: a unit record is a team member's account; a captured Human decision or the completed code takes precedence.

### The size of the recorded work

The supplied file count compares the earlier foundation packet, PKT-A-3, with the current work. “Runtime” means the application's own code. “Supporting” means files used to build and support it. Nonblank lines exclude empty lines.

Limit: these figures measure volume, not quality, test coverage or completed requirements.

| Category | Earlier PKT-A-3: files / lines / nonblank lines | Current PKT-B: files / lines / nonblank lines |
| - | -: | -: |
| Documentation | 270 / 47,989 / 32,119 | 283 / 54,328 / 36,996 |
| Runtime | 112 / 29,620 / 26,874 | 157 / 72,289 / 66,320 |
| Testing | 95 / 25,355 / 22,853 | 188 / 112,995 / 104,368 |
| Supporting | 16 / 3,354 / 3,141 | 25 / 14,528 / 13,823 |
| All counted text | 493 / 106,318 / 84,987 | 653 / 254,140 / 221,507 |
| All tracked files | 538 | 698 |
| Non-text files, identified but not counted in lines | 45 | 45 |


The same counting script was used for both snapshots. It reproduces the earlier Curator's overall totals exactly. The earlier published category counts were 272, 106, 103 and 12 files; this script counts 270, 112, 95 and 16. The earlier category rules are not on file.

Limit: the overall totals agree, but comparisons with the Curator's published categories need that qualification. [Earlier snapshot: `5eb77836`; supplied census and script.]

| Testing location | Files | Total lines | Nonblank lines | Meaning |
| - | -: | -: | -: | - |
| `archive/tests_2026_10/` | 153 | 94,156 | 87,494 | Archived old suite; not run in the automatic GitHub checks |
| `tests/` | 8 | 5,247 | 4,720 | Current tests |
| `verification/` | 26 | 13,517 | 12,106 | Tools for running checks and testing them with deliberate defects |


Most test code is now in the archive. The supplied count reports 98,851 nonblank lines of Python test code: 1.49 lines for each nonblank line of application code. Python is the programming language used here.

Limit: the directory subtotals above do not redefine the testing category, and the ratio does not measure protection. [Source: `06_CENSUS/CENSUS_f829d1b2_v1_0.txt` and its supplied script.]

## 3. What The Packet Was

The earlier packet established ways to preserve and read a history. This packet built on that foundation so people could act through it: join, apply, decide, leave, found an organisation, establish governance and hold an office.

The first design exercise explored human accountability. A community example helped distinguish participation, dissent, absence and authority.

Limit: the example guided design; it was not a condition for deployment or a promise to build every part immediately. [Source: B-0 §§2–4, 9.]

Design, correction, testing tools and working practices developed together. By late September, the team had recorded 39 design layers and settled a candidate for implementation.

Limit: those layers were stages of design, not 39 successful product releases. [Sources: B-2 §§2–7; B-3 §3; B-4 §1; B-5 §1; B-6 §1; B-7 §§3–4.]

## 4. Capability And Constitutional Meaning

The product records the authority behind an organisation's actions. The person entering an action may differ from the organisation acting, the person or body deciding, and the software carrying it out. The Human Governor required those responsibilities to stay distinct. [Sources: B-9 §4; B-10 §6.]

The completed work supports these capabilities:

- **Registering and founding.** A person can register without automatically joining an organisation. Founding can create the organisation, its first membership and its founding Coordinator appointment together. [B-15 §1; B-16 §1.]

- **Applications.** Submission, withdrawal, assignment and decisions have separate records. A decision must follow the permitted process. The first focused set of tests in the rebuilt suite checks that a final outcome cannot be replaced by a conflicting later one. [B-9 §4; B-10 §6; B-36 §1.]

- **Birthing and governance.** Birthing is an explicit act that records the organisation's constitution at that point. Saving a form does not do it accidentally. Governance formation has separately recorded processes before and after Birthing. [B-17 §1; B-19 §1.]

- **Holding an office.** A definition says what the office is. A nomination proposes a person. Consent records that person's response. Appointment records the authorised result. Updating the definition does not rewrite an existing appointment's history. [B-36 §1; landed definition, consent and role services.]

- **Preserving the governing structure.** Board offices remain when vacant. Their type cannot be changed to a custom role to escape that protection. The ordinary process cannot change the Coordinator or reserved platform offices. Limit: further protection against direct database changes remains due. [Forward ledger FW-1a, FW-3 and FW-5; C6 dispositions.]

A subscription can make a feature available. It does not give a person authority to act for an organisation.

Limit: custom offices remain unavailable to users until the missing checks on subscription changes and expiry are established. [Forward ledger FW-6 and FW-12; C6 dispositions.]

## 5. The Road To It

The work progressed from design through implementation to shared product delivery. The table distinguishes those stages. The supplied log identifies 147 saved changes; the unit records explain their meaning and the Human directions behind them.

Limit: an early implementation on a development version was not yet a release into the shared version, called *main*.

| Period | What changed for the project | Evidence and decision |
| - | - | - |
| 15–20 September | Pre-discovery and initial planning examined authority, participation and the inherited history-recording foundation. The Human Governor required the list of actual product actions to lead the design. | B-0 §§2–3; B-1 §§2, 8; B-2 §2. Parallel work on the checking process reached commit `57015b13` on 19 September. |
| 21–28 September | Repeated review exposed omissions and contradictions. The Human Governor identified that a design for Circle coordination had no way for people to join. Recovery, accepting requests and closing work were refined through layer 39. | B-3 §3; B-4 §1; B-5 §1; B-6 §1; B-7 §3. Design candidate v1.27 became the implementation baseline; no product landing was implied. |
| 29 September | Membership, Coordinator offices and applications were joined into working processes on separate development versions. The Human Governor ruled terms, attribution and implementation limits. | B-8 §§4–6; B-9 §§4–6. Implementation commits `a721b3db`, `ed13de03`, `a95a6a67`, `ea4dcc7c`. |
| 30 September–3 October | Review found that recorded responsibility and real permission checks needed further work. Application history, settings and real permission-checking code were integrated. Tools for copying databases were added for development. | B-10 §§5–7; B-11 §§9, 17; B-12 §1. Commits `4b099955`, `a27b7d8c`, `64a3cb0b`, `5cf628ff`, `cf290f29`, `4fee6699`, `44abe706`. |
| 4–5 October | A completed departure could no longer be undone in practice by a competing appointment. A consent withdrawal while appointment waited had to be honoured. | B-13 §1; B-14 §1. Commits `b4fd3e69`, `772e5a5a`, `e9136b3b`, `cc694d80`. |
| 6–11 October | Founding, registration, explicit Birthing and governance formation were built and accepted within their agreed behind-the-scenes boundaries. They still did not provide a complete website experience or authorise operational use. | B-15 through B-19 §1. Commits `bb94c5c4`, `10662970`, `26093aef`, `c68efab7`. |
| 12–15 October | The work added free beta access and subscription-based features. It also made missing usage measurements show as unavailable. Database time and safe repeat requests received separate corrections. | B-20 §§1–3; B-21 §1; B-22 §1; B-23 §1. Commits `ac7aa844`, `f770ec67`, `7de093a1`, `b6fc705a`. |
| 15–19 October — plan change | The Human Governor changed how testing was run, then made automatic GitHub checks the required evidence before accepting code. Repeated long failures exposed gaps in how the checks ran and weak diagnosis. He brought the development work into the shared version before the planned end of the packet. | B-24 §§1–2; B-26 §1; B-27 §1; B-28 §1; B-29 §1; B-30 §1. First whole-route pass reported on `5489b0eb`; gate decision at `39986973`; main landing `ac0e246a`, 19 October. |
| 19–25 October | Requests gained lasting receipt records across application, membership, office, governance and formation acts. The time the system recorded a request became the common reference. Documentation and source review also identified further safeguards to add. | B-31 through B-35 §1. Commits `e832273f`, `49895997`, `de7f6e7a`, `5eee83eb`, `d57ec7f2`, `b9dd69fe`, `32fb4b6d`, `70a89f3d`, `e155bf25`. |
| 26–27 October | Offices chunks C0 and C1 recorded existing refusals and added office definitions with editions, supporting references and database safeguards. Fixed totals for checks of the whole system caused rework. | B-35 §1. Commits `db10965d`, `968d1fe1`, `d29170f2`, `429c9bc5`. |
| 29 October — plan change | The Human Governor stopped item-by-item repair and set aside the old suite. The small interim test suite landed, followed by a focused set of checks for final application outcomes. This changed the testing method during the pilot. | B-35 §1; pilot additions B35 v0.4. Main foundation `35c37a44`; final application-slice commit `c50a5585`. |
| 31 October–1 November | Offices C2–C5 extended safeguards, enabled office-definition changes, expanded permission checks and enabled office actions. Review repairs happened before the required GitHub runs. | B-36 §1. Main landings `2e295bff`, `9e5f706d`, `52f802c7`, `f829d1b2`. |
| 31 October — plan change | The Human Governor noticed that automatic GitHub checks still rebuilt databases repeatedly despite the earlier database-copy design. He required the saving to apply there too, ahead of the next offices step. | Pilot additions B36 v0.1; main landing `04e9df6c`. |
| 1 November | C6 was commissioned to document the completed work, account for unfinished work and catch up this permanent record. The Human Governor assigned the next work on safeguards and the value of tests. | C6 scope capture and dispositions. C6's landing commit and GitHub run are recorded in the changelog. |


## 6. Human Decisions

The Human Governor's decisions shaped both the product and how it was built.

| Decision | Consequence | Source |
| - | - | - |
| Preserve the distinction between dissent and non-participation. | Participation evidence must not silently turn disagreement into absence. | B-0 §§2–4. |
| Work already under decision keeps its agreed authority; a later change must not reopen a final outcome. | Corrections had to be checked across the whole process, not only at the edited point. | B-6 §§1, 7; B-7 §3. |
| Distinguish the organisation acting, the person or body deciding, the software carrying out the act, and the person entering it. | The record of an accepted action names the organisation's act without erasing the responsible human. | B-9 §4; B-10 §6. |
| Require a successful, reviewed GitHub run before accepting each unit into the shared product. | Local and development results remain useful but do not substitute for that required run. | B-27 §1; B-30 §1. |
| Set aside the old tests and rebuild around what the product must protect. | Historical tests remain available for selective reuse, but regular runs of the whole old suite were not retained as a second maintenance burden. | B-35 §1; pilot additions B35 v0.4. |
| Keep board offices permanent and their type fixed; distinguish an office's identity from its displayed name. | Vacancy does not abolish an office. Renaming remains separate work. | Forward ledger FW-1a, FW-2, FW-3; C6 dispositions. |
| Explain errors in human terms and provide a support route when the person cannot resolve them. | Current code identifies the kind of explanation needed; automatic ticket creation remains future work. | Forward ledger FW-4. |
| Use prepared database copies for repeated setup, except when proving that a database can be built from scratch or an exception is agreed. | Repeated setup cost in the automatic GitHub checks was removed through a separately reviewed change. | Pilot additions B36 v0.1. |


The move to a fresh start had several contributors. The Advisor said its proposed reduced suite was not a proven minimum. The Human Governor then asked to start from zero and chose to set the old suite aside entirely. The Vision Chamber's first plan had kept scheduled runs of the old tests.

Limit: the later claim that no artificial-intelligence team member had proposed any fresh start was too broad; the record corrects it. [Source: pilot additions B35 v0.4, milestone account.]

## 7. Exact Implementation Boundary

The current code supports office definitions and office actions beyond the Coordinator. These offices belong to an organisation.

Limit: extending these actions to a Circle, a group within an organisation, remains separate work.

Office definitions keep a history of editions and cite the constitutional provisions relied on. Changing a definition does not create an appointment. Retiring an eligible custom definition does not end a person's appointment: a current appointment or pending nomination prevents retirement. Reassignment ends the old appointment; it does not appoint the successor.

Limit: the code does not decide whether the cited constitutional provisions are currently in force. [Sources: landed definition and role services; forward ledger FW-1, FW-3.]

A read-only lookup reports board-office vacancies as groundwork for CARE, INTEVIA's observation function. Its “filled” result includes a pending nomination.

Limit: “filled” does not prove someone holds the office, interpret the constitution or tell anyone to fill it. [Source: `board_office_vacancies` and `_has_unended_assignment`; FW-9.]

The normal application processes refuse prohibited office changes. More protection is assigned to the immediate safeguard unit. It covers reserved offices, permanent board offices, fixed office types and appointments using outdated definitions.

Limit: refusal in the normal process does not prove that a different path could not write a matching approval and make the change directly in the database. [Source: FW-5 and C6 dispositions.]

Errors are grouped by the help needed: a known way to recover, an explained restriction, or technical support. This lays groundwork for clearer help.

Limit: no support ticket is created yet; the future website must establish what happened and create the ticket before telling the person support is handling it. [Source: FW-4; C3 prepared comparison v1.3, limitation I-1.]

## 8. Verification And Evidence

The automatic GitHub checks provide the required evidence before code is accepted. This process is called continuous integration, or CI. A product test checks behaviour. A deliberate-defect check asks whether a selected test notices a fault introduced on purpose.

The C5 total is **145 checks: 126 product tests and 19 deliberate-defect checks**. Of the product tests, three run without a database and 123 use one. Separate checks of the testing tools are not included. Six admission rules govern new tests, with a report recording how each test meets them.

Limit: a pass does not prove every requirement; known gaps in the test-rule evidence remain assigned for follow-up. [Sources: B-36 §§0–2; landed CI growth table v1.3; C6 dispositions.]

| Unit | Shared-version commit | Qualifying GitHub run | Recorded result |
| - | - | -: | - |
| Offices C1 | `429c9bc5` | 40552391751 | Pass after two earlier GitHub failures; 1,796 tests |
| Fresh-start foundation | `35c37a44` | 40861235163 | First-run pass; 86 product tests |
| Final application-outcome checks | `c50a5585` | 40933172498 | Pass after fixes; 90 product tests |
| Offices C2 | `2e295bff` | 41148959500 | First qualifying GitHub run passed; 100 product tests |
| Offices C3 | `9e5f706d` | 41219076566 | First qualifying GitHub run passed; 110 product tests |
| Database copies in GitHub checks | `04e9df6c` | 41248678339 | First qualifying GitHub run passed; 110 product tests |
| Offices C4 | `52f802c7` | 41264710425 | First qualifying GitHub run passed; 118 product tests |
| Offices C5 | `f829d1b2` | 41308978727 | First qualifying GitHub run passed; 126 product tests |


[Sources: B-35 §1; B-36 §1; pilot additions B36 v0.2.]

The records show the test-rule sign-off process becoming more explicit. Practice item 159 says the specific Human sign-off report was introduced after C3's first GitHub run had already started.

Limit: this is more precise than B-36's summary that every first run had that sign-off beforehand; this account does not repeat that blanket claim. [Sources: B-36 §0; practice items 159–169, item 159.]

The five consecutive first-time passes apply to the final candidates presented to the required GitHub checks. Reviews and corrections helped prepare them. C3, for example, had several design revisions.

Limit: earlier rehearsals used PostgreSQL 16, while the final GitHub checks used PostgreSQL 17; those rehearsals helped diagnosis but did not replace final verification. PostgreSQL is the database software. No product or GitHub test run was performed to produce this documentation draft.

## 9. Findings Carried Forward

The Human Governor gave the remaining work named homes on 1 November. This makes the next steps and access restrictions explicit. The account preserves those assignments.

| Work still owed | Decided home or restriction | Record identifier |
| - | - | - |
| Explicit database protection for reserved and permanent offices, fixed office type and appointments using outdated definitions; safer reading of stored consent results | SR-H, the limited safeguard unit immediately after C6. Database safeguards before user access; stored-result correction before v1.0 release. | FW-5, FW-14 |
| Checks that demonstrate custom-office safeguards, including when a subscription expires | The unit covering subscription access from start to expiry. Custom offices remain unavailable to users until the needed checks are complete. | FW-6, FW-12 |
| Required offices, authorised constitutional removal, re-establishment of a retired office, and CARE observations | The work on current constitutional law in LIBRARY, which keeps knowledge in recorded editions, together with CARE. | FW-1, FW-1a, FW-3, FW-9 |
| Renaming standard governance offices without changing their identity | Standard role types unit | FW-2 |
| Automatic support tickets and honest follow-up notices | SERVICE Support ServiceFlow, the support-workflow unit | FW-4 |
| A test using a real reserved-office record created without bypassing safeguards | The platform-founding unit, named GENESIS | FW-11 |
| Stronger tests, checks that distinguish the participation rules, investigation of archived failures, and missing test records | Test-value review; Vision Chamber repeats the search for overlapping tests in C6 | FW-13, FW-15, FW-16 archived note; TAR gaps A–C |
| The order in which Circle actions are refused | The later unit for Circle actions | FW-16 Circle note |


“FW” means forward work item; each number identifies an entry in the work ledger. “TAR” identifies the test admission rules and their adherence report. Gaps A–C are outstanding testing and record checks.

The C6 source review found FW-7's corrections to fact checking in place. It also found six of FW-10's seven points present. The remaining point concerns which help functions the code explicitly lists as available to other code. The main function for choosing the kind of help is listed. A separate function translating database refusals into messages is not.

Limit: FW-10 is therefore partial; the Vision Chamber owns the ledger update, and the C6 v1.0 return gives the exact file and line evidence. Shared error wording remains unchanged. [Sources: forward ledger v0.7; C6 dispositions; C6 return §3.]

The wider handover also identifies the next work beyond offices: checking outstanding v1.0 obligations, the first pilot review, documentation packets 51–61 awaiting follow-through, reviewing visibility into the agents' work, consolidating records, and later email and walkthrough work.

Limit: a successful offices run does not complete those items. [B-36 §5.]

## 10. Corrections Recorded Against The Seats

The team corrected gaps in design, implementation and its own accounts. Independent review found problems the designer and earlier reviewer had missed. These corrections remain part of the delivery story.

- **Returning to the sources and the whole process.** Early summaries drifted from Human rulings. Checks at an edited point missed other paths through the same process. The recorded remedy was to revisit the exact sources and the whole journey. [B-4 §7; B-5 §7; B-6 §7.]

- **Adding real permission checks.** Review found that early tests supplied a substitute permission component, but the product lacked the real one. The missing product code was added. [B-11 §9; implementation commits `5cf628ff`, `cf290f29`.]

- **Changing how failures were investigated.** The first test reset changed how tests ran without resolving what the whole suite checked. Three long failed runs were followed by individual repairs without finding their common cause. The Human Governor moved diagnosis to another team member. [B-24 §2; B-26 §1; B-35 §1.]

- **Stopping an unsustainable repair cycle.** Repairing and reducing the old suite did not reach an affordable outcome. The Human Governor changed the approach. The record holds the artificial-intelligence team accountable for challenging it late, rather than attributing the problem only to the founder's inexperience. [Pilot additions B35 v0.4.]

- **Carrying the database-copy saving into GitHub.** Database copies were available in development from 3 October but were not used in the automatic GitHub checks. The Vision Chamber explained the repeated setup cost without noticing that inconsistency. The Human Governor called it an artificial-intelligence failure and commissioned the correction. [Pilot additions B36 v0.1.]

- **Repairing C3 before acceptance.** Its first candidate could not reach some intended actions and compared historical records incorrectly. Later revisions moved refusals before permission decisions, protected permanent board offices and closed a way to evade protection by changing an office's type. [C3 prepared comparison records v1.0–v1.4.]

- **Correcting the record itself.** Later records corrected a wrong run name, dates written as 2 November that were actually 1 November, and fragmented practice records. [B-36 §4; forward ledger v0.7 header.]

Limit: successful final runs do not erase these earlier failures, and independent review still needs source checks, rehearsals and Human judgement.

## 11. IDOPT And HAT Significance

The pilot gained evidence of how Human judgement changed the course of development. When continued repair threatened delivery, the Human Governor changed the testing method and asked for the decision to be measured. He also identified the missed database-copy saving and pressed the team to finish within agreed limits. [Pilot additions B35 v0.4; B36 v0.1–v0.2.]

IDOPT (**I**NTEVIA **D**evelopment **O**perating **P**rotocol and **T**hinking) is described here as INTEVIA's development operating protocol: the rules for commissioning, checking and recording work. HAT means the Human–AI Team, with artificial-intelligence team members working under final Human authority.

Limit: these observations do not prove a universal claim about the team model.

The pilot compares what a design promises with what the resulting code provides. It records the promised inputs, outputs and changes to stored information. A record made after standard checks is a checkpoint, not approval. The designer also carried out these comparisons in the offices work.

Limit: prepared comparison records are not completed running-test evidence, and the designer's prior knowledge means those comparisons were not independent. [Pilot seat variation; checkpoint recipe v1.1.]

The record identifies several changes that may have helped: the new test suite, database copies, stronger review before building, tighter limits on repair rounds, and a different designer from C3.

Limit: before-and-after figures cannot separate their effects; the changes must stay visible in the measurement record. [Pilot additions B35 v0.4; B36 v0.2; B-32 §1.]

## 12. Cost And Time Evidence

Checks became substantially faster, and the later offices chunks reached the shared product in a short period. The table separates the checking sequence from the whole GitHub job, which includes other work.

| Recorded point | Product tests | Deliberate-defect checks | Checking sequence | Whole job where supplied |
| - | -: | -: | -: | -: |
| C1 before the old suite was set aside | 1,796 | Not restated here | 46 min 37 s | Not separately supplied |
| Interim foundation | 86 | Not supplied | 2 min 39 s | Not separately supplied |
| C2 | 100 | 17 | 8 min 35 s | Not separately supplied |
| C3, rebuilding databases each time | 110 | 19 | 12 min 38 s | 13 min 16 s |
| Database copies in GitHub checks | 110 | 19 | 3 min 09 s | 3 min 48 s |
| C4 | 118 | 19 | 2 min 57 s | Not separately supplied |
| C5 | 126 | 19 | 3 min 32 s | 4 min 10 s |


[Sources: B-35 §1; pilot additions B36 v0.2; CI growth table v1.3.]

At C5, the checking sequence took 3 min 32 s. The whole job took 4 min 10 s. The database-copy comparison is more closely matched: both runs used 110 product tests and 19 deliberate-defect checks.

Limit: the under-four-minute result is for the sequence, not C5's whole job; smaller-suite speed does not prove equal coverage, and even the matched comparison was not a controlled experiment.

The record shows C3 taking approximately 7.5 hours and C5 taking **3 h 04 min 11 s** from commission to its qualifying GitHub run. C2 to C5 reached the shared product on 31 October and 1 November. The five consecutive final candidates that passed first time were C2, C3, the database-copy change, C4 and C5. Four were offices chunks. [B-36 §§0–1; pilot additions B36 v0.2.]

The earlier repair period had taken Making Engine allowance use from roughly 58% to 119%. Clockify was named as the authoritative Human time ledger.

Limit: no complete financial cost or reconciled Human time total is supplied here. [B-35 §1; B-1 §7.]

The Human Governor's words in the opening record his assessment and hope at this milestone. Continued measurement must track defects missed before release, maintenance cost, remaining protection gaps and test-suite growth. [Pilot additions B36 v0.2.]

## 13. What This Packet Did Not Do

The packet established working foundations and recorded safeguards. Further delivery is needed for a complete website experience, public deployment, release readiness and proof of all obligations. The archived suite remains available for selective reuse; it is not a compulsory recurring job.

The remaining product work includes interpreting current constitutional law, automatic support tickets, naming standard offices, the outstanding Circle actions and the complete custom-subscription expiry process. Immediate database safeguards are still required before users can access offices.

Limit: a historical test pass is not current coverage after the reset. [B-36 §5; C6 dispositions; FW-1–FW-16.]

C6 prepares documentation for review. It changes no product behaviour and adds, changes or retires no test.

Limit: checks of the document edits do not replace independent review, the Making Engine's checks when applying the files, or the eventual GitHub run.

## 14. Authority Boundary And Pending Human Disposition

The Human Governor approved the name **Governed Organisations, Membership and Offices** on 1 November 2026. He also approved this one plain-English pass, followed by Gemini 4 Argon's review in two steps. The proposed filename therefore stands. [Human Governor decisions D1 A and D2 A, 1 November 2026.]

The Vision Chamber checks claims against the evidence. The Reviewer reviews the candidate. The Making Engine then applies and verifies the approved files. C6's qualifying GitHub run and landing are recorded in the changelog.

The Human Governor's authorisation to commit the Datacron constitutes acceptance under the standing ruling. The next agreed design work is the limited offices safeguard unit, followed by the test-value review. [C6 commission; scope decisions and dispositions, 1 November.]

Limit: accepting this record does not close §9's open work or adopt new product scope or policy.

Final authority remains with **Carmian Owen, Human Governor**.

## Glossary

Technical terms and record labels are kept here so the story can use everyday words.

| Term or record label | Plain-English meaning |
| - | - |
| Datacron | A lasting account of what was built, its evidence, decisions and unfinished work. |
| PKT-B; PKT-A-3 | Packet B of the organisation work; its earlier foundation packet A, part 3. |
| S015; US015; B-0…B-37 | The organisation development slice, its stream identifier, and numbered conversation or unit records. B-25 was skipped. |
| CORE; ORGANISM | Shared application foundations; the organisation product area. |
| Living Organism; LO | INTEVIA's term for an organisation; LO is its abbreviation in source files. |
| GOVERNANCE Circle | An organisation's recorded governing group. A Circle is a group within the organisation. |
| Coordinator | An office whose eligible holder may perform specified organisation actions. |
| Birthing | The explicit act completing an organisation's formation under the applicable recorded conditions. |
| SR; C0–C6; C5 | The offices workstream and its numbered delivery chunks. C5 enables non-Coordinator office actions; C6 documents the work. |
| SR-H; hardening | The next limited offices safeguard unit; strengthening protection against invalid actions. |
| FW-1…FW-16 | Numbered forward-work entries. Section 9 records their meaning and assigned homes. |
| TAR; TAR-1 to TAR-6; gaps A–C | Test admission rules and their adherence report; the six rules for new tests; outstanding test-value, overlap-search and record evidence. |
| CI; CI-CLONE-1 | Continuous integration: automatic checks on GitHub; the unit that introduced database copies into those checks. |
| Qualifying run | The run whose result counts as the required evidence before accepting the code. |
| Verification route; whole job | The sequence of checks; the complete GitHub job, including its other work. Their times differ. |
| Clone mode; cloning | Making a disposable copy of a prepared database instead of rebuilding it each time. |
| Mutation entry; mutation check | A deliberate defect paired with a check expected to detect it; running that check to see whether it does. |
| Pure test; database-backed test | A test that runs without a database; a test that uses one. |
| PostgreSQL; Python | The database software; the programming language used by the application and checks. |
| Commit; main; branch; landing | A precisely identified saved code version; the shared version; a separate development version; accepting work into the shared version. Commit identifiers in tables and citations refer to the supplied log. |
| Authority provider | The part of the software that checks whether an action has the required permission. |
| Service; composed service routes | Code carrying out an operation; operations joined into a working process. |
| Database guard; writer | A database rule that refuses an invalid change; code or another caller making a change to stored information. |
| Durable request intake | Recording a request so there is a lasting account of its receipt. |
| Receipt; receipt time; retry | A lasting record of an accepted request or completed result; the time the system records the request; repeating a request without a second effect. |
| Definition; code; version; kind | What an office is; its stable internal identifier; an edition of its definition; its type. The code is not its human-facing title. |
| Entitlement lifecycle | How access to a subscription-based feature starts, changes and ends. It does not itself give authority to act for an organisation. |
| Witness | A test selected to demonstrate a particular required safeguard or behaviour. |
| CARE; LIBRARY; SERVICE; Support ServiceFlow | The observation function; the area that keeps knowledge in recorded editions; the service area; the planned support workflow. These are product names. |
| GENESIS | The platform's own founding work, distinct from ordinary organisation office actions. |
| IDOPT (**I**NTEVIA **D**evelopment **O**perating **P**rotocol and **T**hinking) | INTEVIA's development operating protocol and thinking: the rules for commissioning, checking and recording work. Earlier records call it IDOP. |
| HAT; AI | Human–AI Team; artificial intelligence. |
| Standard-checks checkpoint; SCC | A record point after standard checks and before comparing design promises with the candidate. It is not acceptance. |
| Contract comparison | Comparing the design's promised inputs, outputs and changes to stored information with what the candidate code provides. |
| Test doubles | Substitutes supplied by tests for real product components. |
| Isolated fixes | Repairs to individual symptoms without investigating their shared cause or the full process. |
| Fresh-build proof | A check that a database can be built from scratch, rather than only copied from a prepared one. |
| Human Governor; Lead Designer; Advisor; Reviewer; Making Engine; Vision Chamber | Final Human authority; designs and specifies; advises independently; reviews; applies and runs; coordinates and checks claims. |
| v0.1, v0.2, v1.0 and similar | Document or candidate versions, not automatic acceptance or product release. |
| SHA-256 | A file fingerprint used to identify its exact contents. |
| I-1 | The C3 comparison record's limit on interim support wording; it does not prove incident logging or reversal of changes in every failure. |

