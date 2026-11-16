# The C Series — Governed Circles, Laws, Decisions and Time

Datacron draft v0.2 · 16 November 2026 · written in channel ALPHA-PREP-1, after the C series' last landing

v0.2 = v0.1 (`4686f79b9214b95a494b824adcaa21e69efe23c10b5f99be5a3e1d0a2bd5d0c0`) + the Human Governor's Clockify time in §12, from his reports of 16 November. Nothing else changed.

A Datacron is a lasting account of what was built, why it changed and what remains owed. This one covers the thirteen development channels US015-C-1 to US015-C-13, from 2 to 16 November 2026. It runs from the first organisation chunk after packet B closed to the last TIMED EVENTS landing before the Alpha build. It is drawn from those channels' unit records, the records they name, and the shared code on GitHub.

## In plain words

In two weeks INTEVIA moved from an organisation that could be founded, joined and given offices to one that can **govern itself on the record**. Among other things, an organisation can now:
- run working groups, called **Circles**;
- hold its own **laws** as governed records, and let its members read the laws in force;
- tie its **objectives** to those laws;
- **discuss** in a forum, and **decide by vote**, counted under its own rules;
- **elect** the people its constitution names;
- form **committees** with exactly the authority its Board gave them;
- keep **voting on** while a Board is active;
- let **deadlines catch up by themselves**, read from **one clock**, the INTEVIA Clock.

Each of these is a capability behind the scenes. None yet has a screen. [Sources: C-1 to C-13 §0 or "What landed".]

The way the team worked changed as much as the product. The team learned to run GitHub's whole set of checks on its own machine before every commit. It learned to stop review loops that discovered a design's edges one finding at a time. And it learned to land each unit through one reusable set of scripts. [Sources: C-2 §0; C-10; C-11; C-12; practice items 197, 222, 223, 228.]

The record shows:

- **27 units landed on the shared version** in 14 days, 3 to 16 November.
- **24 of the 27 passed their first qualifying GitHub run.** Circles, R and L each passed on its second. The last 18 units, from LIB to the committee fixes, passed first time in a row.
- **Product tests rose from 152 to 322**, and deliberate-defect checks from 19 to 31, all under the six test admission rules.
- **56 database migrations** were added (0054 to 0109).

Limits: a first-time pass followed design correction and review, not just coding. A test count measures volume, not coverage. The suite now takes about 25 minutes on GitHub, against about 5 at the start. [Sources: C-1 §2; C-13 "What landed"; CI growth table v1.23; repository log.]

At the series' last landing the Human Governor read the quality record himself:

> "We are 18-3-18...or 36/39 first-time passes. That is becoming a very healthy quality success metric for the October test method and inventory reboots."

That is his count across the October rebuild, quoted as given (C-13). This Datacron's own count, over the C series alone, is 24 of 27.

## 1. Status

The C series delivered INTEVIA's governance core behind the scenes:
- Circles;
- laws in force;
- Guiding Intentions;
- the DISCUSSION forum;
- decisions by vote;
- elections;
- committees;
- Board continuity;
- TIMED EVENTS with the INTEVIA Clock.

Each unit landed on the shared version, called **main**, through a qualifying GitHub run and the Human Governor's acceptance. Main now stands at commit `ba7a42fd`, with core migration head `0109_committee_chair_reservation`. [Sources: C-13 "What landed"; ALPHA-PREP-1's read-only check of main.]

Limits:
- This is not a website experience. Screens remain a later pass.
- It is not approval for user access, and it does not close every owed item (§9).

Under the Human Governor's ruling of 19 October, committing this Datacron, or authorising its commitment, is his acceptance of it. No separate ceremony is required. Accepting the record does not accept unfinished product work.

## 2. Identity

This record covers the thirteen channels US015-C-1 to US015-C-13. Each closed with a unit record, kept in Drive under `IDOP v0.9.7/Unit Records`. Before this Datacron was written, each was checked to be byte-identical to its staging copy.

| Unit record | Days | SHA-256 |
| - | - | - |
| C-1 | 2–4 Nov | `393aa598373188344826adc38918ab0528d76947003fabc4840ee08da91a6164` |
| C-2 | 4–6 Nov | `d3bfb83ed79078b49b18da85817b7e4a37c266143252fe1315c16a1051cdd7fc` |
| C-3 | 6–7 Nov | `14c1383a42082abddcbd233391228a1d50f0f4b034c904886f67f7c156f17cab` |
| C-4 | 7–8 Nov | `a88de050e9821dce924fc4894add673a15238961859b56b426f4b2c141c56167` |
| C-5 | 8–9 Nov | `8ec09278d849b9b33f7b4627db3fe644fa8739697bb7d7db9bb9203db4eabde1` |
| C-6 | 9 Nov | `af7a94189a83aaf82c3637c5a250dbb01d9cba7a22a73cc271aa66ffa2f47037` |
| C-7 | 10 Nov | `056dacc21e2f67b0b105509859deb1ade8b6d3e832f1a1a53ed92573b9768c2a` |
| C-8 | 10–12 Nov | `6bcd27599ac581dc4a91c085b0a84fc6a0ecab96e252c0b23604d767e3bed5d0` |
| C-9 | 13 Nov | `7af237172450b6bc7d47827dfd9167febd8f6aac421cf1d3729f1878c33c5100` |
| C-10 | 15 Nov | `182af7e03a610b25ab44596c8b98c2b2af7f543d0fb5d65caa91197316641329` |
| C-11 | 15 Nov | `80d9b8c4f2eb32400e57d1f5251503e72eedb991257d6bf738d88406d617f078` |
| C-12 | 16 Nov | `cb1467c694b85c5a5491bcc9883eddb4931d1932eb2e0f5115bca649f7ffc459` |
| C-13 | 16 Nov | `5954bf3cba772fe937375e6579392ca9393c65eb00d85e993390b248c9a90193` |

Limit: a unit record is a seat's account. A captured Human decision, or the landed code, takes precedence.

**Where the series sits.** The PKT-B Datacron, *Governed Organisations, Membership and Offices*, reached offices step C5 at commit `f829d1b2`. Between it and C-1 came channel B-37's closing work on 1 and 2 November:
- the offices documentation (C6);
- the database safeguards before offices reach users (SR-H);
- the PKT-B closing record;
- the front-end foundation, F0;
- the CI actions' update;
- the documentation of packets 51 to 61;
- the email foundation, EM-a.

The PKT-B closing record carries the first three. The last four have no Datacron of their own. This record names them only as the base C-1 started from (main `ebfdc88`).

### The repository at this commit

The same census script was used for all three snapshots (`VC_LINE_CENSUS_v1_0.py`, `1dbb870d…9ad9`). At `f829d1b2` it reproduces the PKT-B Datacron's figures exactly, so all three columns compare. "Runtime" means the application's own code; nonblank lines exclude empty lines.

| Category | PKT-B at `f829d1b2`: files / lines / nonblank | C-1's start at `ebfdc88` | Now at `ba7a42fd` |
| - | -: | -: | -: |
| Documentation | 283 / 54,328 / 36,996 | 290 / 55,254 / 37,592 | 300 / 76,804 / 59,020 |
| Runtime | 157 / 72,289 / 66,320 | 180 / 75,247 / 68,984 | 296 / 118,144 / 109,027 |
| Testing | 188 / 112,995 / 104,368 | 195 / 114,958 / 106,228 | 253 / 142,034 / 131,389 |
| Supporting | 25 / 14,528 / 13,823 | 44 / 16,570 / 15,707 | 49 / 17,002 / 16,088 |
| All counted text | 653 / 254,140 / 221,507 | 709 / 262,029 / 228,511 | 898 / 353,984 / 315,524 |
| All tracked files | 698 | 759 | 948 |

Over the series, the application's own code grew by about 40,000 nonblank lines, from 68,984 to 109,027.

| Testing location at `ba7a42fd` | Files | Total lines | Nonblank lines | Meaning |
| - | -: | -: | -: | - |
| `archive/tests_2026_10/` | 153 | 94,156 | 87,494 | The suite set aside in October; not run by GitHub's checks, unchanged since |
| `tests/` | 58 | 32,478 | 30,028 | The live suite, up from 8 files at PKT-B |
| `verification/` | 41 | 15,325 | 13,819 | The tools that run the checks and test them with deliberate defects |

The live suite grew from 4,720 to 30,028 nonblank lines.

Limit: these figures measure volume, not quality, coverage or completed requirements. The testing category includes the archive, which no longer runs. [Source: the census, run by the Vision Chamber on 16 Nov 2026 at all three commits.]

## 3. What The Series Was

PKT-B gave INTEVIA people, organisations and offices. The C series gave those organisations a way to act together under their own rules, and to keep acting when people leave.

The work followed one arc:
- groups inside an organisation (Circles);
- what the organisation is bound by (its laws);
- how it talks (DISCUSSION) and decides (the vote);
- whom it elects, and what its committees may do;
- what happens to the Board when members stand it down;
- what time it is, on one clock;
- what happens when deadlines pass.

The Human Governor set the order as work arrived. [Sources: C-2 §5; C-3 §5; C-4 §5; C-5 §0; C-6 §5; C-7 §5.]

## 4. Capability And Constitutional Meaning

In each item below, "is" describes the landed behaviour behind the scenes.

- **Membership and authority that survive people leaving.** A person can leave and rejoin as a new membership period. A Coordinator can leave holding offices, and mentorship offers and probation are recorded. An organisation that loses its last Coordinator has a recovery route. Authority can end, lapse, be recused or be handed over, without losing an applicant's place or changing the rules mid-decision. [C-1 §§0, 2: C1a, C1b, C2, C3.]

- **One way to write history.** A single shared writer appends events to every protected chain. [C-1 §0; C-2 §2: C1-16.]

- **INTEVIA's own organisation and working groups.** INTEVIA's root Living Organism (R) and ordinary Circles are built: formed, joined, coordinated, and kept from being stranded. [C-2 §§0, 2: Circles, R.]

- **The organisation's own law.**
  - An organisation's law is held as governed records (L).
  - Its members can read the laws in force at their exact version (LIB).
  - A Policy can be published, then adopted (LIB).
  - Every Guiding Intention, a strategic objective, cites a law in force (GI).

  The Human Governor called this the first two links of the Constitutional Automation chain. [C-2 §0.]

- **Discussion and the vote.**
  - Every organisation and Circle has a forum (DISC1).
  - A member's Suggestion moves through preparation and a Clarity stage to a vote, and every step goes onto the organisation's record (DISC2).
  - A vote's result can carry out the act it decided: adopting a law, a Policy version or a Guiding Intention (DISC3).
  - The rules of voting can themselves be changed by the members' vote (DISC4).
  - Every result traces to the rule it was counted under, and that rule to the vote that set it.

  Internally the vote is called CQ; users are to see "vote". [C-3 §0; C-4 §0.]

- **Elections the constitution names.** Elections are held only for places a constitution names, and counted under its rule (ELECT). The result is the authority for exactly that person, place and term. Offices end on time, a successor elected in advance takes over exactly at the end, and existing charities' sitting trustees are recognised as they stand (ELECT P3). [C-5 §0; C-6 §0.]

- **Committees with exactly the authority they were given.** A Board can form committees grounded in its own law, at four levels of delegated authority; owed row C1-129 records that it should be three levels, not four. A committee's level is checked when it acts and again when its decision takes effect. Committee work stays with the seat. [C-7 §0.]

- **Boards protected from themselves.**
  - While a Board is active, voting stays on. Refusals to switch it off are written to the record.
  - The members can stand the Board down by vote, and restore it.
  - It becomes active again only when the constitution's minimum number of trustees is elected (C1-108).

  The Human Governor's words: "INTEVIA, protecting boards from as much insanity as it can". [C-6 §0; C-8 §0.]

- **Time that keeps itself.**
  - Due work catches up after a member's request, one item at a time, never waiting on locked work, each effect leaving a receipt. There is no scheduling service to run or pay for (TIMED EVENTS T1a).
  - There is **one clock**, the INTEVIA Clock: every stored and written read of the time goes through it (CK-1, CK-2 storage, CK-2 code).
  - In owned test databases only, the clock can be moved forward, so deadlines are tested, not assumed.
  - A Circle that loses its last Coordinator gets a 720-hour recovery period, goes dormant at its end, and can be reseated.
  - A dormant Circle can no longer decide for a committee.

  [C-8 §0; C-9; C-10; C-11; C-12; C-13.]

Limits:
- TIMED EVENTS is switched off on every host until a host rehearsal passes there.
- Notices and email from TIMED EVENTS (T2) are not built.
- No feature here has a screen yet.
- Legal compliance is not claimed. [C-8 §0; C-13 "What is open".]

## 5. The Road To It

| Dates | Channel | What reached main | Evidence |
| - | - | - | - |
| 2–4 Nov | C-1 | C1a rejoin, the psycopg pin, C1b leave with offices, C2 coverage and recovery, C3 authority lifecycle | Main `395f53ba`; 152 → 185 product tests |
| 4–6 Nov | C-2 | C1-16 shared writer; Circles; R root organisation; L laws; LIB laws in force and Policy; GI Guiding Intentions | Main `991cc09f`; 185 → 225 tests. Circles, R and L failed their first CI runs |
| 6–7 Nov | C-3 | DISC1 forum; DISC2 Suggestion to vote | Main `b264862a`; 268 tests |
| 7–8 Nov | C-4 | DISC3 result as authority; TE test economy; DISC4 voting rules by vote | Main `38eabfdd`; TE folded 276 tests to 239 |
| 8–9 Nov | C-5 | ELECT elections; HARD ready | Main `1050f2f5` |
| 9 Nov | C-6 | HARD one source for voting authority; ELECT P3 term ends and resignation | Main `8c8ce25f` |
| 10 Nov | C-7 | COMMITTEES | Main `e96f30d4`; 254 tests |
| 10–12 Nov | C-8 | C1-108 voting stays on; TIMED EVENTS T1a | Main `b85df0db`; 267 tests |
| 13 Nov | C-9 | CK-1 the INTEVIA Clock | Main `85cc7c99`; 276 tests |
| 15 Nov | C-10 | CK-2 storage: everything stored reads the Clock | Main `8a4efb8c`; 279 tests |
| 15 Nov | C-11 | CK-2 code: everything in Python reads the Clock | Main `f663207f`; 293 tests |
| 16 Nov | C-12 | Circle dormancy and recovery | Main `10e89d54`; 316 tests |
| 16 Nov | C-13 | Committee fixes at dormancy (C1-145) | Main `ba7a42fd`; 322 tests |

Two points in this road changed the plan:
- **C-5, 8 November.** The Human Governor set the Board Policy Manual arc: HARD, the ELECT P3 decision, committees, TIMED EVENTS, then the BPM release.
- **C-13, 16 November, 12:47.** With an estimated 8 to 9 more hours in the clock-witnesses chunk, he chose to stop building and land what was built. The remaining witnesses are owed, and TIMED EVENTS closes for the end-of-November deploy. [C-5 §0; C-13 "What changed during the day".]

## 6. Human Decisions

| Decision | Consequence | Source |
| - | - | - |
| Only being without a Coordinator restricts an organisation. The Coordinator is an INTEVIA mechanism, not a constitutional office; a vacant governance office restricts nothing. | Recovery is built around the Coordinator, not around the constitution's offices. | C-1 §3 |
| CARE, INTEVIA's assistance and observation function, works through DISCUSSION and SERVICE, on the record; every object can have its discussion. | CARE has no private chat window; its guidance is visible and recorded. | C-1 §3 |
| The trustee's-question storyboard is Alpha's principal test case. | Scenes are placed in the owed ledger so that the units building them read them. | Rulings D-1 to D-8, 4 Nov, captured in C-2 |
| Run GitHub's whole check sequence locally before any commit, on one baseline set up the same way in every channel. | After three first-run CI failures, every later unit passed CI first time. | C-2 §§0, 4; items 197, 198 |
| A setting earns its place; the convergence rule (only a defect against a ruling reopens a build). | DISC2 landed from its seventh build instead of looping. | C-3 §§0, 3–4; items 206, 209 |
| Remove the CI time envelope; keep the control chart, normalised for runner speed. | A slow GitHub runner is no longer read as a heavier suite. | C-5 §§0, 3; item 212 |
| TIMED EVENTS runs inside Django, woken by signed-in activity, one due item per request, with no broker. | No separate service to run, pay for or keep in step. | C-5 §§0, 3 |
| Voting stays on while a Board is active; members may stand a Board down and restore it. | Entrenched; built as C1-108. | C-6 §0; C-8 §0 |
| Swap the Lead Designer and Advisor seats between the two model lineages for TIMED EVENTS, and back for dormancy, to balance usage. | Each lineage both designed and reviewed. | C-8 §1; C-11 "What TIMED EVENTS still owes" |
| One clock: after the INTEVIA Clock is built there is no second, test-only clock. | Every time read in storage and code was converted; tests move the one clock. | C-9 |
| No serial correction rounds; at most two review passes per chunk, with the evidence agreed before the build. | The chunks after it landed within two passes. | C-10; C-11; items 222, 223 |
| Measure the trade of time for quality by where later defects' root causes fall. | Every Alpha defect records its root-cause commit, severity and reach (PM-C1-1, item 224). | C-11 |
| One reusable landing kit. | The last two landings used it with no script adapted. | C-12; C-13; item 228 |
| Users never see "CQ"; the default word is "vote". Edge cases go to the owed ledger by default. | Standing practice for all later work. | C-13; items 230, 231 |
| Stop building, land what was built, and aim for an internal deploy at the end of November. | T1c, T2 and the remaining clock witnesses are deferred to Alpha. | C-13 |

## 7. Exact Implementation Boundary

- **Behind the scenes only.** Every capability in §4 is a service on main, with its database guards and tests. The website shows only the front-end foundation and an events list. [ALPHA-PREP-1 domain check, plan v0.4 §2.]
- **What the vote decides.** A vote can carry out an attached law, Policy version, Guiding Intention or settings change. A plain decision records an approval; there is no payments area, so INTEVIA records a payment's approval but does not move money. [C-3 §0; C-4 §0.]
- **TIMED EVENTS.** It is switched off by default on every host. It serves its listed purposes:
  - election term end, resignation and vacancy;
  - vote preparation and response deadlines;
  - committee mandate end;
  - the Coordinator-recovery notice and phase;
  - the Circle consumer.

  It sends no notice or email (T2), and does not yet serve EVENTS or SERVICE. [C-8 §0; C-9 "What TIMED EVENTS still owes".]
- **The clock in tests.** The moving clock exists only in owned test databases. INTEVIA refuses to serve a database that has ever held it. [C-9 "What it does".]
- **The older domains.** LIBRARY's base, EVENTS, SERVICE and EDUCATION, built before organisations existed, were not changed by the series. Only LIBRARY's laws and Policy joined the organisation. [ALPHA-PREP-1 domain check.]

Limit: refusal on the normal route does not prove that every other database path refuses. Each guard is named in its unit's records.

## 8. Verification And Evidence

The qualifying evidence for every unit is a GitHub CI run through a pull request. That run checks the product tests, the isolation of each test, and every deliberate-defect check; the unit then lands by a hash-checked fast-forward of that exact commit.

| Measure | At C-1's start | At C-13's landing |
| - | -: | -: |
| Product tests | 152 | 322 (plus the SR round-trip control) |
| Deliberate-defect checks | 19 | 31 |
| Qualification controls | — | 38 of 38 |
| Isolation checks applicable and established | — | 302 of 302 |
| Units landed in the series | — | 27 |
| First qualifying run passed | — | 24 of 27 |

[Sources: C-1 §2; C-13 "What landed"; CI growth table v1.23.]

**The local whole job.** Since 5 November, every candidate first ran CI's whole job on the Vision Chamber's baseline (item 197). From the L unit's second run on, the local decision file matched CI's byte for byte. At the last landing, the local run, the PR run and main's run on `ba7a42fd` all produced the same decision file, `2de9aed8…0dfa0`. [C-2 §0; C-13; ALPHA-PREP-1 `ap1main`.]

**Main's runs.** Every main push run that was read passed; R's and L's main runs were not downloaded. For the last landing, main's run took 39 min 09 s against the PR run's 25 min 40 s on identical code. That difference is GitHub's runner alone (factor 1.493). [VC_CHECK_CI_T1B_C1_145_MAIN_RUN_v1_0.md.]

Limits:
- A pass shows the checked properties on the tested commit, not every requirement.
- The suite is much smaller than the archived one, and is not proven equivalent to it.
- No test run was made to write this Datacron. Its figures come from the records named.

## 9. Findings Carried Forward

At the series' close, the open work has named homes in the owed ledger (v1.97).

| Work still owed | Home | Owed row |
| - | - | - |
| TIMED EVENTS T1c (Policy review commitments), T2 (notices, Coordinator notifications, email) and the performance dashboard | Deferred to Alpha | C1-124, C1-105 |
| The remaining clock witnesses, and shorter committee tests | Deferred to Alpha; all edge reach | C1-154, C1-155 |
| The SERVICE read defect N72-1 | Folded into Alpha's SERVICE work | C1-140 |
| The rarer SERVICE read defect N72-10 | Deferred to Alpha (edge) | C1-140 |
| "Vote", not "CQ", in about 30 landed messages; later the organisation's own label | Its own unit, before Alpha's user-facing review | C1-153 |
| A Board request link from a reserved committee chair's refusal | With the one-click request from a refusal | C1-151, C1-133 |
| An unvalidated committee appointing body | Next committee work | C1-152 |
| The code Reviewer's six text notes on the last landing | Next work on those files | C1-156 |
| Committee owed defects, witnesses and the remaining committee scope | The next unit touching committee files | C1-115, C1-119, C1-102 |
| Dependency security updates (Django, PostgreSQL, Python) | First part of the Alpha build | C1-150 |
| The documentation bank: units banked, not yet applied | A CI window | C1-86 |
| Consequences for breaching constitutional law | Forward work | C1-65 |

Limit: an owed row is a record of what is due, not a plan with dates. The Alpha readiness plan v0.4 places what Alpha needs.

## 10. Corrections Recorded Against The Seats

The HAT intervention log records each error at the event. These are the ones that changed how the team works.

- **Stating before checking.** The Vision Chamber stated facts and figures it had not yet checked at source. Items 181 and 183 followed. [C-1 §4.]
- **Running and patching another seat's candidate.** The Vision Chamber ran a candidate five times while patching it itself: 51 minutes. Item 202 followed: run once, return the defects. [C-2 §4.]
- **Three first-run CI failures, then none.** Circles, R and L each failed their first CI run on a check only part of CI covered. Running CI's whole job locally (item 197) ended it. [C-2 §§0, 4.]
- **Build loops.** DISC2 took seven builds; ELECT P3 found failing steps one round at a time. The convergence rule (209) and tracing journey tests before the first run (213) followed. [C-3 §4; C-6 §4.]
- **A direction too large for its seat.** The first committees direction went out unsplit, and the Lead Designer's channel froze overnight and was lost. The Human Governor ruled it serious and avoidable. Every Lead Designer direction is now split into boxed parts with stage reports (item 216). [C-7 §6.]
- **A long run where no test could see the change.** A 22-minute local run on a documentation-only change led to item 218: no run without its rule and cost stated first. [C-7 §6.]
- **Three seats passed a guarantee-level defect; the fourth found it.** In TIMED EVENTS T1a, the code Reviewer's second step found a waiting path the Advisor's final review and the Vision Chamber's checks had passed. [C-8 §6.]
- **Eleven consecutive MUST FIX returns.** CK-2's one-clock check (CLK-05) was corrected round after round, each finding discovering the next edge. The Human Governor ended serial rounds (item 222), then set a hard budget of two review passes with the evidence agreed first (item 223). The next chunk landed within its two passes. [C-10; C-11.]
- **The Vision Chamber's own errors in a review pass.** Three of the seven MUST FIX findings in CK-2 code's first pass were the Vision Chamber's own. [C-11.]
- **A claim about a set of runs checked over one run.** A reading said no test passed 45 s when main's run had one. Item 226 now computes such claims over every run named. [C-11.]
- **A carrier missing the current practice ledger.** Item 227 now makes every carrier hold what its relay tells the seat to read. [C-12.]
- **A prompt read without bounds.** The Making Engine read its prompt unbounded, and its tool spilled output under AppData; it reported this itself. Item 232 now tells every Making Engine prompt to read in windows. [C-13.]

Limit: successful landings do not erase these failures. The log holds rows HI-C1-1 to HI-C1-198 and HP-C1-1 to HP-C1-213.

## 11. IDOPT And HAT Significance

IDOPT is INTEVIA's development operating protocol: the rules for commissioning, checking and recording work. HAT is the Human–AI Team, with the AI seats working under final Human authority.

- **The practice ledger grew from item 170 to item 232 in the series.** Each item names its source event. An item proposed is *in play* until an event proves it, then *standing* (item 79). [Practice v0.82.]
- **Human judgement steered.** The C-3 record notes that every point that changed DISC2's design came from the Human Governor, while the AI seats carried out the work and each caught what another missed. His words: "Well said. This is the HAT." [C-3 §0.]
- **Independent review found what running did not.** In CK-1, ten findings came from reading after every run had passed. [C-9.]
- **He named the cost of his own decision.** Of items 222 and 223: "I basically forced time as a constraint on a process previously led on quality." He asked for it to be measured (PM-C1-1). [C-11.]
- **A process change has to endure.** "Changes are only good if they are going to endure." This is the test the reusable landing kit was built against. [C-12.]

Limit: these are observations of one team over two weeks, not proof of a general claim about the team model. The pilot is ongoing, and its results will be reported later.

## 12. Cost And Time Evidence

| Point | Product tests | Seconds per test (PR run) | Route on GitHub (PR run) |
| - | -: | -: | -: |
| C1a, C-1's first landing | 157 | about 1.0 | job 5 min 17 s |
| DISC1 | 248 | 2.35 | 12 min 25 s |
| COMMITTEES | 254 | 4.39 | 22 min 49 s |
| TIMED EVENTS T1a | 267 | 4.90 | 26 min 26 s |
| Circle dormancy | 316 | 4.43 | 27 min 33 s |
| Committee fixes (C1-145) | 322 | 4.11 | 25 min 40 s |

[Sources: CI growth table v1.23; control chart reading `VC_READING_SPC_T1B_C1_145_v1_0.md`.]

- **The control chart has settled.** Since COMMITTEES the process is stable at a higher level: phase 3, mean 4.60 s per test, upper limit 5.53 s. Normalised for runner speed, the cost per test has been flat since TIMED EVENTS T1a. The Human Governor adopted phase 3 as the baseline. [C-11; control chart point 40.]
- **The run on the same code varies.** Main and PR runs of one commit have differed from 40% faster to 52% slower. One run cannot show a change smaller than that. [CI growth table v1.23.]
- **Chunk time.** Circle dormancy took about 11 hours. CK-2 code took about 1 h 45 min from its first review return to its push. [C-11; C-12.]

**The Human Governor's time.** Clockify is his time record. His detailed November report (exported 16 Nov) has 99 entries. The table attributes each to its channel by the task name he logged it under. Four entries logged without a channel name are matched to their units by description: C0, the design contract, and C2 and C1a/C1b to C-1; LIB to C-2.

| Channel | Main work | Human time |
| - | - | -: |
| C-1 | C0 design contract; C1a, C1b, C2, C3; C1-16 | 19 h 35 min |
| C-2 | Circles, R, L, LIB, GI | 33 h 09 min |
| C-3 | DISC1, DISC2 | 14 h 06 min |
| C-4 | DISC3, TE, DISC4 | 11 h 24 min |
| C-5 | ELECT; HARD built | 8 h 58 min |
| C-6 | ELECT P3; HARD landed | 7 h 10 min |
| C-7 | COMMITTEES | 9 h 50 min |
| C-8 | C1-108; TIMED EVENTS T1a | 24 h 57 min |
| C-9 | CK-1, the INTEVIA Clock | 11 h 11 min |
| C-10 | CK-2 storage | 17 h 49 min |
| C-11 | CK-2 code | 6 h 17 min |
| C-12 | Circle dormancy | 9 h 21 min |
| C-13 | Committee fixes at dormancy | 5 h 35 min |
| **The series** | **27 units** | **179 h 28 min** |

- **15 working days, 2 to 16 November.** Thirteen of them logged 9 hours or more; the longest, 10 November, logged 16 h 25 min.
- **About 6 h 39 min of his time for each unit landed.**
- **TIMED EVENTS took 65 h 16 min,** over a third of the series, from T1a (C-8) to the committee fixes (C-13). CK-2 storage alone took 17 h 49 min; it held the eleven consecutive correction rounds (§10). CK-2 code, the first chunk under the two-pass budget, took 6 h 17 min.
- **For scale:** October, the month of the testing reset, logged 419 h 43 min, mostly on testing method and rework (October summary report).

Limits:
- Clockify records one activity at a time. Work running in parallel (a review in one seat while another seat builds) is logged under whichever task was running, so it is not double-counted. Nor is it split between the parallel tasks.
- The figures are the Human Governor's time only. AI seat time and usage costs are not included.
- The attribution follows the task names he logged; some entries cross a channel's boundary.

[Sources: Clockify detailed report 01/11/2026–30/11/2026 (`9926d33263fd6925439b70cc1828b36671a15ad44ef32528d793b176527472e6`, as exported 16 Nov; report total 201:41:40, which also covers 1–2 Nov work before the series and ALPHA-PREP-1); Clockify summary reports for November (`bfc18508faa4802328e068625b535911dbc8c86b267af9e068983460bc5286d8`) and October (`afff46f5dcd32b649d5d4b27200d3e9e5c03532fd0aa7befe01c02cc7963aebc`).]

## 13. What This Series Did Not Do

- It built no screens. Organisation, Circle, office, forum, vote and law pages remain for the front-end work.
- It did not bring LIBRARY's base, EVENTS, SERVICE or EDUCATION into the organisation: they still belong to a person, with no production authority. This is Alpha's main domain work.
- It sent no TIMED EVENTS notice or email, and did not switch TIMED EVENTS on anywhere.
- It did not interpret a constitution. INTEVIA applies a cited provision exactly, and records the citation.
- It did not finish the clock witnesses, and it did not apply the documentation bank.

## 14. Authority Boundary And Pending Human Disposition

- The Human Governor asked for this Datacron on 16 November 2026: "Can we build a Datacron for the build until this point? That will give us the C channels through to TIMED EVENTS, and be a good threshold."
- **The name** *The C Series — Governed Circles, Laws, Decisions and Time*, and its filename, are proposed for his approval.
- **Review.** By past practice a Datacron draft is checked against its sources by a Reviewer before he accepts it. Each revision is a new file with its own digest.
- **Acceptance.** His authorisation to commit the Datacron is his acceptance, under the standing ruling of 19 October.

Limit: accepting this record does not close §9's open work or adopt new product scope.

Final authority remains with **Carmian Owen, Human Governor**.

## Glossary

| Term | Plain-English meaning |
| - | - |
| Datacron | A lasting account of what was built, its evidence, decisions and unfinished work. |
| US015-C-1 … C-13; C-1 §0 | The thirteen development channels of this series; a section of a channel's unit record. |
| Unit record | A channel's own account of its work, written at its close. |
| PKT-B; PKT-C | Packet B (organisations, membership, offices); packet C, the organisation work this series began with. |
| Living Organism; LO | INTEVIA's term for an organisation. |
| Circle | A working group inside an organisation. A dormant Circle is paused until reseated. |
| Coordinator | The INTEVIA office that keeps an organisation or Circle running. It is not a constitutional office. |
| Board; GOVERNANCE Circle | An organisation's governing body, as recorded in INTEVIA. |
| Law; Policy; Bylaw | An organisation's rules, held as governed records with exact versions. |
| Guiding Intention; GI | A strategic objective, tied to a law in force. |
| DISCUSSION; Topic; Suggestion | The forum; a thread in it; a proposal that can go to a vote. |
| Vote; CQ; Clarity | A decision by the members. CQ is its internal name, never shown to users. Clarity is the step where members confirm they understand the wording. |
| ELECT; HARD; TE | The elections unit; the hardening unit; the test-economy unit. |
| C1-108 | The unit that keeps voting on while a Board is active. |
| TIMED EVENTS; T1a; T1b; T1c; T2 | The engine that lets deadlines catch up by themselves, and its four landings. |
| INTEVIA Clock; CK-1; CK-2 | The one clock every time read goes through; its build and its two-part conversion. |
| CLK-05 | The standing check that no time is read outside the Clock. |
| C1-145; C1-33 | The committee fixes at dormancy; Circle dormancy and recovery. |
| Owed ledger; C1-n | The record of work due; each numbered row. |
| CI; qualifying run; main run | GitHub's automatic checks; the run whose pass is the evidence before landing; the run on main after landing. |
| Main; commit; landing | The shared version; a precisely identified saved version; accepting a commit into main. |
| Migration | A numbered change to the database's structure. |
| Deliberate-defect check | A fault put in on purpose to show a test notices it. |
| Isolation check | A check that a test left nothing behind for the next one. |
| Control chart; runner factor | A chart of seconds per test over time; how much faster or slower one GitHub machine ran the same tests. |
| Edge; ordinary use | Reachable only by an unusual input, sequence or timing; reached in ordinary use. |
| Landing kit | One reusable set of scripts that commit, open, watch and land any unit. |
| IDOPT; HAT; AI | INTEVIA's development operating protocol; the Human–AI Team; artificial intelligence. |
| Human Governor; Vision Chamber; Lead Designer; Advisor; Reviewer; Making Engine | Final Human authority; coordinates and checks claims; designs and builds; reviews independently; reviews code or documentation; commits and pushes. |
| SHA-256 | A file fingerprint that identifies its exact contents. |
