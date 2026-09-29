# PKT-B SLICE 3 — DESIGN EXTRACT — for the Making Engine

Extracted verbatim by US015-B-9 (Vision Chamber), 29 September 2026, from `PKT_B_S3_SOURCE_PACK_v1_0.md` (237,070 B, SHA-256 `9e7c3bd1a00032b892b0be54e89d72efb153eb562bf43258d163785b5151d0e8`). That pack holds whole sections of the implementation baseline's operative reading, byte for byte: `CANDIDATE_v1_27_OPERATIVE_READING.md`, 1,282,633 B, SHA-256 `07d629f7344d165f0962639a447a171cb44b3a7ade237e1b188a21fff356ffef`.

Each paragraph below is reproduced whole, from its opening line to the line before the next paragraph, with its trace and status lines. Trailing blank lines are dropped. The reading's line numbers are given for each. **Where this extract and the reading differ, the reading governs.**

**Status words matter:**
- **DECIDED** is binding.
- **PROPOSED** is the design as it stands — not adopted.
- **OPEN** is an undecided Human question: build nothing that decides it.
- **LIMIT** states what the design's evidence does not establish.
- **HISTORY** is not operative.

## Also applies — already committed at `docs/s015/pkt_b/` on the baseline

- **S1's extract,** `PKT_B_S1_DESIGN_EXTRACT_v1_0.md` (`b381fe99…5c7f`): ⟨B.049⟩ (the membership edges); ⟨E2.112⟩ (CD-3 — rejoining); ⟨E2.114⟩ (probation — MP-9 in outline); ⟨C.037⟩–⟨C.040⟩ (membership and office are separate facts; current memberships).
- **S2's extract,** `PKT_B_S2_DESIGN_EXTRACT_v1_0.md` (`b3f9d0dc…9e32`): ⟨C.023⟩ (the multi-row commands and their actors); ⟨C.051⟩ (four distinct acts); ⟨C.056⟩ (the rule home); ⟨E1.016⟩–⟨E1.019⟩ (K-3 and the founding boundary).

## The routes — what is implementable now (the Vision Chamber's marking, to be confirmed or corrected by the preflight)

| route | depends on | now |
|---|---|---|
| **R1** — the LO decides through CQ | DISCUSSION's CQ records (opening, participant set, result, a status as of coordinates) — **no DISCUSSION or CQ model is landed**; `cq_active` (PE-08), with no settings store landed (HD-E6 OPEN); ⟨E2.124⟩: "a missing setting is No" | **not implementable** — an implementation limit, owned by DISCUSSION's interface and the settings store |
| **R2** — the LO Coordinator self-assigns the CSA | PF-01's assignment row (PKT-B's); SERVICE's CSA workflow (what landed SERVICE records can supply is a preflight question); the Coordinator's effective office (S2) | **the candidate route** — subject to the preflight's authority and attribution mapping |
| **R3** — the APPLICATION Circle decides through CQ | as R1, plus an established, ELIGIBLE APPLICATION Circle and Circle membership (PC-11, S4) | **not implementable** — DISCUSSION, and S4 |
| **R4** — an APPLICATION Circle member self-assigns | Circle membership (PC-11, S4); the sole-participant setting (no settings store) | **not implementable** — S4 |

**Probation commencement** (⟨E2.114⟩; MP-9 at ⟨E2.115⟩–⟨E2.116⟩, not reproduced) is not implementable now. It depends on the LO's `probation_enabled` setting (no settings store landed, S2b) and on the `PROBATION` subscriber capability (A-15 entitlement, not landed).

## Paragraphs, and why each is here

- **⟨E2.002⟩** (reading lines 3354–3362) — the four decision routes; PC-08; Part G §§4–5 pins (DECIDED) — which route decides, and on what authority
- **⟨E2.003⟩** (reading lines 3364–3370) — the four decision routes; PC-08; Part G §§4–5 pins (DECIDED) — which route decides, and on what authority
- **⟨E2.004⟩** (reading lines 3372–3382) — the four decision routes; PC-08; Part G §§4–5 pins (DECIDED) — which route decides, and on what authority
- **⟨E2.005⟩** (reading lines 3384–3404) — the four decision routes; PC-08; Part G §§4–5 pins (DECIDED) — which route decides, and on what authority
- **⟨E2.006⟩** (reading lines 3406–3411) — period bookkeeping per representation — CD-3's R3 is live; the period row is S4's (boundary)
- **⟨E2.007⟩** (reading lines 3413–3421) — PF-02, the sole-participant setting (default No); PF-01's assignment and competing claims — R2's sole-Coordinator case
- **⟨E2.008⟩** (reading lines 3423–3437) — PF-09 (a)–(d)′ — the four application relations; the freeze (B-G9 reads settings_basis); route status supplied by the route's owner
- **⟨E2.009⟩** (reading lines 3439–3446) — PF-09 (a)–(d)′ — the four application relations; the freeze (B-G9 reads settings_basis); route status supplied by the route's owner
- **⟨E2.010⟩** (reading lines 3448–3456) — PF-09 (a)–(d)′ — the four application relations; the freeze (B-G9 reads settings_basis); route status supplied by the route's owner
- **⟨E2.011⟩** (reading lines 3458–3463) — the CSA route's PENDING / UNABLE at read coordinates; B-G9 admits the assignee's act
- **⟨E2.012⟩** (reading lines 3465–3474) — the CSA route's PENDING / UNABLE at read coordinates; B-G9 admits the assignee's act
- **⟨E2.013⟩** (reading lines 3476–3481) — the CSA route's PENDING / UNABLE at read coordinates; B-G9 admits the assignee's act
- **⟨E2.014⟩** (reading lines 3483–3492) — the CSA route's PENDING / UNABLE at read coordinates; B-G9 admits the assignee's act
- **⟨E2.016⟩** (reading lines 3503–3510) — B-G9′ — re-engagement refused while PENDING
- **⟨E2.018⟩** (reading lines 3521–3530) — notices and re-engagement checked together; completable; decided / withdrawn — at most one terminal row
- **⟨E2.019⟩** (reading lines 3532–3536) — notices and re-engagement checked together; completable; decided / withdrawn — at most one terminal row
- **⟨E2.020⟩** (reading lines 3538–3542) — notices and re-engagement checked together; completable; decided / withdrawn — at most one terminal row
- **⟨E2.022⟩** (reading lines 3551–3565) — PF-09 (e) — interfaces, owners, dependencies (DISCUSSION CQ; SERVICE CSA) — the boundary
- **⟨E2.023⟩** (reading lines 3567–3573) — O-4 — the status read and the admission test, separated; the eligibility rule; the walk (probe cases); the CQ distinction
- **⟨E2.024⟩** (reading lines 3575–3582) — O-4 — the status read and the admission test, separated; the eligibility rule; the walk (probe cases); the CQ distinction
- **⟨E2.025⟩** (reading lines 3584–3587) — O-4 — the status read and the admission test, separated; the eligibility rule; the walk (probe cases); the CQ distinction
- **⟨E2.026⟩** (reading lines 3589–3596) — O-4 — the status read and the admission test, separated; the eligibility rule; the walk (probe cases); the CQ distinction
- **⟨E2.027⟩** (reading lines 3598–3609) — O-4 — the status read and the admission test, separated; the eligibility rule; the walk (probe cases); the CQ distinction
- **⟨E2.028⟩** (reading lines 3611–3624) — O-4 — the status read and the admission test, separated; the eligibility rule; the walk (probe cases); the CQ distinction
- **⟨E2.029⟩** (reading lines 3626–3630) — O-4 — the status read and the admission test, separated; the eligibility rule; the walk (probe cases); the CQ distinction
- **⟨E2.030⟩** (reading lines 3632–3636) — O-4 — the status read and the admission test, separated; the eligibility rule; the walk (probe cases); the CQ distinction
- **⟨E2.031⟩** (reading lines 3638–3661) — O-4 — the status read and the admission test, separated; the eligibility rule; the walk (probe cases); the CQ distinction
- **⟨E2.032⟩** (reading lines 3663–3672) — §2A — read coordinates: the rule for every status, steps 0–6, demonstrations; admission at its own coordinates
- **⟨E2.033⟩** (reading lines 3674–3679) — §2A — read coordinates: the rule for every status, steps 0–6, demonstrations; admission at its own coordinates
- **⟨E2.034⟩** (reading lines 3681–3697) — §2A — read coordinates: the rule for every status, steps 0–6, demonstrations; admission at its own coordinates
- **⟨E2.035⟩** (reading lines 3699–3702) — §2A — read coordinates: the rule for every status, steps 0–6, demonstrations; admission at its own coordinates
- **⟨E2.036⟩** (reading lines 3704–3711) — §2A — read coordinates: the rule for every status, steps 0–6, demonstrations; admission at its own coordinates
- **⟨E2.037⟩** (reading lines 3713–3719) — §2A — read coordinates: the rule for every status, steps 0–6, demonstrations; admission at its own coordinates
- **⟨E2.038⟩** (reading lines 3721–3726) — §2A — read coordinates: the rule for every status, steps 0–6, demonstrations; admission at its own coordinates
- **⟨E2.039⟩** (reading lines 3728–3747) — §2A — read coordinates: the rule for every status, steps 0–6, demonstrations; admission at its own coordinates
- **⟨E2.040⟩** (reading lines 3749–3755) — §2A — read coordinates: the rule for every status, steps 0–6, demonstrations; admission at its own coordinates
- **⟨E2.041⟩** (reading lines 3757–3765) — §2A — read coordinates: the rule for every status, steps 0–6, demonstrations; admission at its own coordinates
- **⟨E2.042⟩** (reading lines 3767–3773) — §2A — read coordinates: the rule for every status, steps 0–6, demonstrations; admission at its own coordinates
- **⟨E2.043⟩** (reading lines 3775–3785) — W-FIN — withdrawal finality (DECIDED, the 23 Sep decision (1)); its scope
- **⟨E2.044⟩** (reading lines 3787–3793) — W-FIN — withdrawal finality (DECIDED, the 23 Sep decision (1)); its scope
- **⟨E2.045⟩** (reading lines 3795–3805) — §2B part 1 — B-G9′, the one limb (UNABLE); everything else refused
- **⟨E2.046⟩** (reading lines 3807–3811) — §2B part 1 — B-G9′, the one limb (UNABLE); everything else refused
- **⟨E2.047⟩** (reading lines 3813–3817) — §2B part 1 — B-G9′, the one limb (UNABLE); everything else refused
- **⟨E2.048⟩** (reading lines 3819–3829) — §2B part 1 — B-G9′, the one limb (UNABLE); everything else refused
- **⟨E2.049⟩** (reading lines 3831–3838) — §2B part 1 — B-G9′, the one limb (UNABLE); everything else refused
- **⟨E2.050⟩** (reading lines 3840–3871) — §2B part 2 — re-application is a new application; the predecessor link; what it does not do
- **⟨E2.051⟩** (reading lines 3873–3891) — §2B part 2 — re-application is a new application; the predecessor link; what it does not do
- **⟨E2.052⟩** (reading lines 3893–3898) — §2B part 2 — re-application is a new application; the predecessor link; what it does not do
- **⟨E2.053⟩** (reading lines 3900–3904) — §2B part 2 — re-application is a new application; the predecessor link; what it does not do
- **⟨E2.054⟩** (reading lines 3906–3919) — §2B part 2 — re-application is a new application; the predecessor link; what it does not do
- **⟨E2.055⟩** (reading lines 3921–3932) — §2B part 3 — B-G10, the withdrawal guardian, terms (a)–(d); its temporal scope
- **⟨E2.056⟩** (reading lines 3934–3938) — §2B part 3 — B-G10, the withdrawal guardian, terms (a)–(d); its temporal scope
- **⟨E2.057⟩** (reading lines 3940–3943) — §2B part 3 — B-G10, the withdrawal guardian, terms (a)–(d); its temporal scope
- **⟨E2.058⟩** (reading lines 3945–3954) — §2B part 3 — B-G10, the withdrawal guardian, terms (a)–(d); its temporal scope
- **⟨E2.059⟩** (reading lines 3956–3962) — §2B part 3 — B-G10, the withdrawal guardian, terms (a)–(d); its temporal scope
- **⟨E2.062⟩** (reading lines 4024–4031) — what stands; OPEN AF-3 (1) and (2); consequential changes; LIMITS and demonstrations
- **⟨E2.063⟩** (reading lines 4033–4040) — what stands; OPEN AF-3 (1) and (2); consequential changes; LIMITS and demonstrations
- **⟨E2.064⟩** (reading lines 4042–4054) — what stands; OPEN AF-3 (1) and (2); consequential changes; LIMITS and demonstrations
- **⟨E2.065⟩** (reading lines 4056–4066) — what stands; OPEN AF-3 (1) and (2); consequential changes; LIMITS and demonstrations
- **⟨E2.067⟩** (reading lines 4088–4111) — what stands; OPEN AF-3 (1) and (2); consequential changes; LIMITS and demonstrations
- **⟨E2.068⟩** (reading lines 4113–4126) — what stands; OPEN AF-3 (1) and (2); consequential changes; LIMITS and demonstrations
- **⟨E2.069⟩** (reading lines 4128–4133) — W10 — the mirror recording order (DECIDED, CD-13); the boundary of W-FIN
- **⟨E2.070⟩** (reading lines 4135–4139) — W10 — the mirror recording order (DECIDED, CD-13); the boundary of W-FIN
- **⟨E2.071⟩** (reading lines 4141–4154) — W10 — the mirror recording order (DECIDED, CD-13); the boundary of W-FIN
- **⟨E2.072⟩** (reading lines 4156–4160) — W10 — the mirror recording order (DECIDED, CD-13); the boundary of W-FIN
- **⟨E2.073⟩** (reading lines 4162–4176) — W10 — the mirror recording order (DECIDED, CD-13); the boundary of W-FIN
- **⟨E2.074⟩** (reading lines 4178–4184) — W10 — the mirror recording order (DECIDED, CD-13); the boundary of W-FIN
- **⟨E2.075⟩** (reading lines 4186–4207) — LIMIT — the clock is a model assumption; its owed obligations
- **⟨E2.076⟩** (reading lines 4209–4222) — MP-12 — G-1: no application holds two standing terminal rows; the mechanism under §6.5's root lock
- **⟨E2.077⟩** (reading lines 4224–4259) — MP-13 — the withdrawal-request evaluation (DECIDED AV-2 direction); DQ-18…DQ-24 incl. DQ-21 OP-W
- **⟨E2.078⟩** (reading lines 4261–4272) — MP-13 — the withdrawal-request evaluation (DECIDED AV-2 direction); DQ-18…DQ-24 incl. DQ-21 OP-W
- **⟨E2.079⟩** (reading lines 4274–4277) — MP-13 — the withdrawal-request evaluation (DECIDED AV-2 direction); DQ-18…DQ-24 incl. DQ-21 OP-W
- **⟨E2.084⟩** (reading lines 4340–4364) — MP-1 hold and resumption under R1-D13 (B-G9 (vi), (vii)); MP-6 deadlines — PF-01's completion_due_at (DECIDED R1-D17, HQ-1)
- **⟨E2.085⟩** (reading lines 4366–4391) — MP-1 hold and resumption under R1-D13 (B-G9 (vi), (vii)); MP-6 deadlines — PF-01's completion_due_at (DECIDED R1-D17, HQ-1)
- **⟨E2.088⟩** (reading lines 4439–4451) — placement — the application record and its link; the withdrawal relation; B-G10 a guardian; CD-6 reading (a) DECIDED: the application relations stand OUTSIDE §5.4
- **⟨E2.089⟩** (reading lines 4453–4463) — placement — the application record and its link; the withdrawal relation; B-G10 a guardian; CD-6 reading (a) DECIDED: the application relations stand OUTSIDE §5.4
- **⟨E2.093⟩** (reading lines 4502–4508) — placement — the application record and its link; the withdrawal relation; B-G10 a guardian; CD-6 reading (a) DECIDED: the application relations stand OUTSIDE §5.4
- **⟨E2.094⟩** (reading lines 4510–4531) — placement — the application record and its link; the withdrawal relation; B-G10 a guardian; CD-6 reading (a) DECIDED: the application relations stand OUTSIDE §5.4
- **⟨E2.095⟩** (reading lines 4533–4547) — placement — the application record and its link; the withdrawal relation; B-G10 a guardian; CD-6 reading (a) DECIDED: the application relations stand OUTSIDE §5.4
- **⟨E2.123⟩** (reading lines 4956–4962) — §8 — Human decisions surfaced by Block E2, with dispositions (DECIDED / OPEN)
- **⟨E2.124⟩** (reading lines 4964–4967) — §8 — Human decisions surfaced by Block E2, with dispositions (DECIDED / OPEN)
- **⟨E2.125⟩** (reading lines 4969–4972) — §8 — Human decisions surfaced by Block E2, with dispositions (DECIDED / OPEN)
- **⟨E2.126⟩** (reading lines 4974–4979) — §8 — Human decisions surfaced by Block E2, with dispositions (DECIDED / OPEN)
- **⟨E2.127⟩** (reading lines 4981–4984) — §8 — Human decisions surfaced by Block E2, with dispositions (DECIDED / OPEN)
- **⟨E2.128⟩** (reading lines 4986–4989) — §8 — Human decisions surfaced by Block E2, with dispositions (DECIDED / OPEN)
- **⟨E2.129⟩** (reading lines 4991–4994) — §8 — Human decisions surfaced by Block E2, with dispositions (DECIDED / OPEN)
- **⟨E2.130⟩** (reading lines 4996–5001) — §8 — Human decisions surfaced by Block E2, with dispositions (DECIDED / OPEN)
- **⟨E2.131⟩** (reading lines 5003–5008) — §8 — Human decisions surfaced by Block E2, with dispositions (DECIDED / OPEN)
- **⟨E2.132⟩** (reading lines 5010–5015) — §9 — the Block's authority boundary

## Not reproduced from the pack (23 paragraphs), and why

- **⟨E2.015⟩, ⟨E2.017⟩, ⟨E2.086⟩, ⟨E2.087⟩, ⟨E2.090⟩, ⟨E2.091⟩** — the held and waiting notices, and the CARE notice relations. They form the CARE interface, not S3's first build.
- **⟨E2.021⟩** — premium ending during an engaged application (entitlement; DISCUSSION).
- **⟨E2.060⟩, ⟨E2.061⟩, ⟨E2.066⟩** — the removed resubmission mechanism's history and dispositions.
- **⟨E2.080⟩–⟨E2.083⟩** — correction acts (MP-2, MP-3 and ruling A's DQ-25). S3 writes no correction act.
- **⟨E2.092⟩, ⟨E2.096⟩** — a naming observation, and §5.4's cardinality (not CD-6).
- **⟨E2.111⟩** — a heading only.
- **⟨E2.112⟩, ⟨E2.114⟩** — in S1's extract.
- **⟨E2.113⟩** — HISTORY.
- **⟨E2.115⟩–⟨E2.117⟩** — MP-9 (probation, not implementable now) and MP-11 (unused membership tokens).

Final authority remains with Carmian Owen, Human Governor.

---

**⟨E2.002⟩** · trace: L01 LD_RETURN_BLOCK_E2_UNIT_4_MEMBERSHIP_ROUTES_RECOVERY_DISSOLUTION_v1_0.md lines 13–18
· status: **PROPOSED — layer 1 (§1 heading; PC-08 one decision per application; route table header)**

## 1. THE FOUR DECISION ROUTES — EACH DEMONSTRATED END TO END

Every route ends in one `core_applicationdecision` row (PC-08, `UNIQUE (application_reference)`) and at most one membership effect. Records shown as *chain: event (actor · enterer)*.

| route | precondition (evaluated at the application's decision-in-progress record, never re-evaluated silently) | records | fold reads |
|---|---|---|---|

---

**⟨E2.003⟩** · trace: L01 LD_RETURN_BLOCK_E2_UNIT_4_MEMBERSHIP_ROUTES_RECOVERY_DISSOLUTION_v1_0.md lines 19–22
· status: **PROPOSED — layer 1 (routes R1–R4); DECIDED behind them — U-1, C-1, Part G §§4–5 (layer 3 §4 adds the Part G citations to each precondition cell: R1 → §4 ¶1–2; R2 → §4 ¶2; R3 → §4 ¶1, ¶3, §5 ¶2; R4 → §4 ¶3 — L3 04 lines 76–79); route R4's qualifying fact is an eligible A-11 participation row under CD-6 (a)**

| **R1 — LO decides through CQ** | `cq_active = Yes` (LO setting, PE-08) **and** collective qualification available for this decision (eligible population > 1; DISCUSSION's arithmetic) | DISCUSSION: the CQ's own records → `supporting_result_reference` · PKT-B: `applicationdecision(route=CQ, deciding_party=LO, outcome)` (actor **the LO** · enterer CARE) · membership `PROPOSED → ACTIVE` naming the decision (actor the LO · enterer CARE) | member from the effect's `effective_at`; the decision's authority = the CQ result + the setting's fold at the decision's coordinates |
| **R2 — LO Coordinator self-assigns the CSA** (CQ inactive, **or** the sole-Coordinator case with CQ configured active — C-1) | `cq_active = No`, **or** exactly one eligible participant who holds effective `COORDINATOR` authority | SERVICE: CSA created · PKT-B: `csaassignment(application, assignee membership, assignee office, effective_at)` (**PF-01**, partial `UNIQUE (application) WHERE effective`) · `applicationdecision(route=CSA_SELF_ASSIGNED, deciding_party=LO, assignment_reference)` (actor the LO · enterer the Coordinator or CARE) · the effect | as R1; the decision-maker's identity and qualifying office are on the assignment row |
| **R3 — APPLICATION Circle decides through CQ** | Circle established and `ELIGIBLE` (E1 §4); `cq_active` inherited; collective qualification available in the Circle | as R1 with `deciding_party = the Circle` (PC-05 `kind = CIRCLE`) | Circle-attributed decision; the LO's membership effect names it |
| **R4 — APPLICATION Circle member self-assigns** (CQ inactive **or** the sole-participant setting permits) | `cq_active = No`; or `application_sole_participant_delegation = Yes` **and** the Circle's eligible population is one **and** that member holds the Circle's Coordinator requirement (C-1) | as R2 with the Circle as deciding party; the assignee's **Circle membership** (PC-11) is the qualifying fact — **not an LO office** `[H — C-1]` | Circle-attributed |

---

**⟨E2.004⟩** · trace: L03 04_INTEGRATION_AND_DISPOSITION.md lines 74–80
· status: **PROPOSED — layer 3 (§4 R-E2: the Part G citations added to E2 §1's precondition cells and the PF-09 heading — EG-3 pins)**
· note ⟦editorial⟧: Layer 3 §4 withdraws layer 2 E1-1 and E2-1 as changes (no draft citation existed in Return 1).

| Return 1 E2 §1 row | citation added to the precondition cell |
|---|---|
| R1 — LO decides through CQ | `[H — Part G §4 ¶1–2; C-1 "CQ availability and inheritance"]` |
| R2 — LO Coordinator self-assigns | `[H — Part G §4 ¶2; C-1 "Sole-Coordinator LO route"]` (the "sole-Coordinator case" already cites C-1) |
| R3 — APPLICATION Circle through CQ | `[H — Part G §4 ¶1, ¶3; §5 ¶2; C-1 "APPLICATION Circle authority"]` |
| R4 — Circle member self-assigns | `[H — Part G §4 ¶3; C-1 "Explicit sole-participant delegation setting"]` (already `[H — C-1]`) |
| PF-09 heading (correction §E2-Q2) | `[H — Part G §4 ¶5; C-1 "Design and review follow-through"]` |

---

**⟨E2.005⟩** · trace: L03 01_SOURCE_RECONCILIATION.md lines 87–102
· status: **DECIDED — Part G §§4–5 (issued, controlling LD-Q10) as pinned by paragraph (layer 3 EG-3): the obligations each route derivation and PF-09 rest on, with relay 4's H-3 correction of Part G §5**

## 5. EG-3 — Part G, paragraph pins; relay 4's H-3 correction of §5

Part G (consolidated relay lines 315–390) as the issued, controlling LD-Q10 (commission v1.5 line 94; Vision Chamber RECONCILED_STATUS line 29). Paragraphs cited, by number within the section:

| obligation the correction cites | Part G pin |
|---|---|
| CQ is a capability available to every Organism; Circles inherit the LO's CQ choice; configuration versus availability | §4 ¶1 (with C-1 "CQ availability and inheritance") |
| sole eligible participant with valid LO Coordinator authority → Coordinator/CSA route while CQ stays configured active; once the population permits CQ, use CQ; non-response creates no sole-participant authority | §4 ¶2 (with C-1 "Sole-Coordinator LO route", "Two-participant qualification") |
| APPLICATION Circle delegated routes; CSA eligibility not restricted to LO Coordinators; C-1's setting, default and alternative route; absent setting is not permission; sole-member Circle meets its Coordinator requirement | §4 ¶3 (with C-1 "APPLICATION Circle authority", "Explicit sole-participant delegation setting") |
| K-2 governs LO Coordinator loss; a valid APPLICATION Circle continues its route while Without Coordinator; later Deactivated restrictions specified separately | §4 ¶4 |
| **"Population or setting changes must not silently transfer authority for work already in progress"** — PF-09's obligation | **§4 ¶5** (and C-1's follow-through sentence) |
| sole-participant route not universal (K-5) | §4 ¶6 |
| APPLICATION creation under subscription and the enabling setting; founding act, membership, required Coordinator; "specify when applications transfer to it"; lapse follows C-3 | §5 ¶2 |
| relay 4 H-3 correction of §5 | "Record the original C-3 accurately: its specific closure and completion provisions were APPLICATION-specific … Correct Part G §5's historical restatement accordingly." The extended principle (no new premium work; bounded completion or handover) reaches APPLICATION, FINANCE and OPERATIONS, each with its own boundaries; APPLICATION's C-3 decision and handover rules preserved |

**Consequence for the correction's E1-1 and E2-1.** E1 needs no citation change (already pinned by paragraph). E2 §1 gains the citations above on its R1 and R3 rows (CQ availability → §4 ¶1–2 and C-1), R2 (→ §4 ¶2, C-1) and R4 (→ §4 ¶3, C-1), and PF-09's heading cites §4 ¶5 and C-1. The "draft … not promoted" deletion is void (it was never in Return 1). Stated as the replacement in 02 §R-E2.

⟦editorial⟧ Period bookkeeping added beneath each route's records column (layer 2 E2-3 → 05 §5); under CD-3 the R3 row is live (see Block C §2A):

---

**⟨E2.006⟩** · trace: L02 04_BLOCK_E2_REVISED_SECTIONS.md lines 38–40
· status: **PROPOSED — layer 2 (E2-3: the period-row inserts per representation; R3 live under CD-3; R1/R2 HISTORY)**

**Replacement location.** Block E2 §1, the route table (routes R1–R4) — add one row-group "period bookkeeping" beneath the records column of each route.

**Replacement text.** **05_R1_R2_R3_SPECIFICATIONS.md §5**: under representation R1 no insert and `period_ordinal = 1` on the `PROPOSED` transition; under R2 nothing beyond the anchor, which carries `period_ordinal = 1`; under R3 one `core_membershipperiod` row (`membership`, 1) inserted in the same transaction before the `PROPOSED` transition, which names it. The four membership-state derivations are unchanged; routes R3 and R4 remain conditional on PC-05 and (route R4) PC-11.

---

**⟨E2.007⟩** · trace: L01 LD_RETURN_BLOCK_E2_UNIT_4_MEMBERSHIP_ROUTES_RECOVERY_DISSOLUTION_v1_0.md line 24
· words replaced from L09 02_REPLACEMENTS_AND_ADDITIONS.md line 244
· words replaced from L10 01_REPLACEMENTS_AND_ADDITIONS_L10.md line 46
· status: **PROPOSED — layer 1 (PF-02 the sole-participant setting's default No; competing claims — PF-01's partial unique index; negative and incomplete CQ results never activate R2/R4 — C-1 DECIDED) with the completion deadline as replaced by layer 9 RT-34c (R1-D17 DECIDED) and layer 10 RT10-05 (HQ-1 DECIDED); PF-02's default — the recommendation [A] not adopted (OPEN as a default; C-1: a missing setting is No)**
· supersedes: L01 LD_RETURN_BLOCK_E2_UNIT_4_MEMBERSHIP_ROUTES_RECOVERY_DISSOLUTION_v1_0.md line 24; L09 02_REPLACEMENTS_AND_ADDITIONS.md line 244

**The sole-participant setting (C-1) — default and alternative route, for adoption (PF-02):** **default `No`** — a forming LO is never stranded because R2 (the sole-Coordinator LO route) is always available when the Circle cannot decide; the alternative route where the setting is `No` is **R2 with an explicit `route_transfer` event** (PE-05) moving the application from the Circle to the LO, attributed as C-3 requires. **A missing or unestablished setting is `No`** — never permission `[H — C-1]`. **Reasoning `[A]`:** `Yes` by default would let a Circle of one decide admissions under a CQ-active constitution without the LO having chosen that; `No` costs one transfer event per such application. **Competing claims** (U-1): PF-01's partial unique index makes the first committed assignment effective; a second claim is refused; reassignment is an explicit `ENDED` on the assignment row plus a new one, by the Circle/LO Coordinator; a decision naming a superseded assignment is refused at commit (PC-08). **A CSA assignment's completion deadline** — 27 days from assignment by default, subscriber-configurable per route; an overdue trigger, never authority expiry or rejection (R1-D17; MP-6); `completion_due_at` is snapshotted on PF-01's assignment row from the setting in force at assignment; during an R1-D13 pause the clock stops and, on lawful resumption, continues with its remaining time under that recorded arrangement (HQ-1; MP-6 as corrected by layer 10). **Negative and incomplete CQ results** never activate R2/R4 `[H — C-1]`: PC-08 admits `route = SOLE_PARTICIPANT` only against the setting's fold, never against a result.

⟦editorial⟧ PF-09 — application-completion authority (layer 2 E2-Q2, inserted after PF-02) — records, definitions, the freeze, later changes; (d) as replaced by layer 3 R-E2Q2 and corrected through layers 4–13:

---

**⟨E2.008⟩** · trace: L02 04_BLOCK_E2_REVISED_SECTIONS.md lines 64–75
· status: **PROPOSED — layer 2 (PF-09 (a) records: the application record, core_applicationengagement, core_applicationdecision, core_applicationwithdrawal; definitions); DECIDED behind it — Part G §4 ¶5, C-1; CD-6 (a): the four relations stand outside §5.4 (L7 02 §2); the application record gains predecessor_reference NULL (layer 6 §3.1); 'no §5.4 question arises' is true under (a)**

### PF-09 — Application-completion authority (proposed; not adopted)

**(a) Records.** The application's lifecycle is **derived from the existence of rows**, with no state column and no fold, so no §5.4 question arises:

| relation (append-only, four-time grammar) | one row means |
|---|---|
| the application record (PC-08's subject) | submitted |
| `core_applicationengagement(application_id, engagement_ordinal, route ∈ {LO_CQ, LO_CSA, CIRCLE_CQ, CIRCLE_CSA}, deciding_party, engagement_reference, settings_basis, basis_reference, actor, enterer)` — `UNIQUE (application_id, engagement_ordinal)` | the deciding route has been engaged; `engagement_reference` names DISCUSSION's CQ opening record or the CSA assignment (PF-01); `settings_basis` names the LO/Circle settings in force at the engagement's `effective_at` as known at its `recorded_at` |
| `core_applicationdecision` (PC-08, unique per application) | decided |
| `core_applicationwithdrawal(application_id UNIQUE, …)` | withdrawn by the applicant |

**Definitions.** *Engaged* = an engagement row exists. *In progress* (Part G §4's "already in progress") = engaged, no decision, no withdrawal. *Before engagement* an application is submitted but not in progress: population and settings changes govern which route will engage it, and engagement itself is the recorded act that fixes the route — nothing silent.

---

**⟨E2.009⟩** · trace: L02 04_BLOCK_E2_REVISED_SECTIONS.md lines 77–79
· status: **PROPOSED — layer 2 (PF-09 (b) the freeze — B-G9 reads the engagement row's settings_basis, never the live settings fold; (c) what later changes do — never re-routed)**

**(b) The freeze.** The decision row for an in-progress application is admitted (guardian **B-G9**, proposed) only if its `route` and `deciding_party` equal the **current** engagement's (greatest ordinal), and its actor and enterer satisfy that route's completion authority as at the engagement's `settings_basis`: for CQ routes, the CQ named by `engagement_reference` has concluded with a result (DISCUSSION's record, referenced by `supporting_result_reference`); for CSA routes, the decision's enterer holds the CSA assignment named by `engagement_reference`, folding `ACTIVE` at the decision's `effective_at` (Ruling 9: capacity tested at the act's effective time). B-G9 reads the engagement row, **never the live settings fold**.

**(c) What later changes do.** A settings transition (`cq_active`, sole-participant delegation, Circle-type settings) or a population change (a member or Circle participant joining or leaving; a Coordinator lost or restored under K-2/H-4) whose `effective_at` is after the engagement governs applications engaged after it. For an in-progress application the engagement stands. It is never re-routed by a change in settings or population.

⟦editorial⟧ (d) → (d)′ the route status (layer 3 R-E2Q2, superseding layer 2 (d)), with the CSA row and PENDING paragraph as corrected by layers 4 (O-4) and 5 (FC-D4/D6), and the CSA row's 'term expired' clause as replaced by layer 9 RT-34d:

---

**⟨E2.010⟩** · trace: L03 03_APPLICATION_AND_ENTITLEMENT.md lines 87–91
· status: **PROPOSED — layer 3 (R-E2Q2 (d)′: three route-status values supplied by the route's owner; the CQ row — read at coordinates per layer 5 §4)**
· supersedes: L02 04_BLOCK_E2_REVISED_SECTIONS.md line 81

**(d)′ The route status — three values, supplied by the route's owner, never inferred by PKT-B.**

| route | status **COMPLETABLE** | status **PENDING** | status **UNABLE** |
|---|---|---|---|
| `LO_CQ` / `CIRCLE_CQ` | DISCUSSION reports the CQ named by `engagement_reference` **concluded with a result** (positive or negative — a negative result completes the route with a decline, C-1) | DISCUSSION reports the CQ **open** (deliberation or qualification in progress, deadline not reached) | DISCUSSION reports the CQ **concluded without a result** or **unable to conclude** under its own rules (population fell below what its arithmetic needs; deadline passed without qualification; the deciding Circle `CLOSED` or dissolved) |

---

**⟨E2.011⟩** · trace: L05 03_FC_D4_D5_D6_CSA_READ_COORDINATES_AND_ADMISSION.md line 60
· words replaced from L09 02_REPLACEMENTS_AND_ADDITIONS.md line 249
· status: **PROPOSED — layer 5 (FC-D4/FC-D6: the CSA row — PENDING / UNABLE at read coordinates; the act B-G9 admits under correction 04 §E2-Q2 (b), specialised by five terms) with layer 9's clause (R1-D17 DECIDED: an expired completion deadline does not make the route UNABLE — OVERDUE derived, MP-6)**
· supersedes: L03 03_APPLICATION_AND_ENTITLEMENT.md line 92; L04 04_O4_CSA_COMPLETION.md line 42; L05 03_FC_D4_D5_D6_CSA_READ_COORDINATES_AND_ADMISSION.md line 60

> | `LO_CSA` / `CIRCLE_CSA` | no decision row **eligible at `(state_at, known_at)`**; the assignment named by `engagement_reference` folds a **non-terminal** state at `(state_at, known_at)` (`PROPOSED`, `ACTIVE`, `PROBATIONARY`, `SUSPENDED`); the deciding Circle not `CLOSED` and the LO not dissolved **at those coordinates**. **The interval between engagement and the assignee's act.** No notice is due; nothing is re-engaged | no decision row **eligible at `(state_at, known_at)`**; the assignment folds a **terminal** state (`ENDED`, `PRIVACY_TRANSFORMED`) at `(state_at, known_at)` — assignee left the LO or the Circle; (an expired completion deadline does **not** make the route UNABLE — the assignment stays `ACTIVE` and the application PENDING with a derived OVERDUE condition, R1-D17 / MP-6;) or the deciding Circle `CLOSED` / the LO dissolved **at those coordinates**. **The route cannot complete** | **B-G9 admits `NEW` under correction `04` §E2-Q2 (b) — the actor and enterer satisfy the route's completion authority *as at the engagement's `settings_basis`*, and B-G9 reads the engagement row, never the live settings fold — which, for a CSA route, is specialised by these five terms and by nothing that replaces (b):** (i) `NEW.route` and `NEW.deciding_party` equal the current engagement's; (ii) `NEW`'s enterer **holds** the assignment named by `engagement_reference`; (iii) that assignment folds **`ACTIVE`** at `(NEW.effective_at, NEW.recorded_at)`; (iv) the enterer's capacity holds at `NEW.effective_at` (Ruling 9); (v) **no earlier decision row exists — required by PC-08's `UNIQUE (application_reference)`, never required to exist.** The five terms are (b)'s CSA parenthetical made operational; whatever completion authority the `settings_basis` names beyond them (a route disabled in the frozen settings, a deciding party the frozen settings do not recognise) is (b)'s and refuses under (b). Nothing else is read |

---

**⟨E2.012⟩** · trace: L04 04_O4_CSA_COMPLETION.md lines 44–50
· status: **PROPOSED — layer 4 (O-4 mutual exclusivity of PENDING/UNABLE; SUSPENDED is pending; COMPLETABLE is said of the act)**

**Mutual exclusivity.** At any `(state_at, known_at)` with no decision row, the assignment's fold is either terminal or not, so exactly
one of PENDING / UNABLE holds (probe case 9: a 10 × 10 coordinate grid across an assignment's `ACTIVE → ENDED` history yields only
those two, one at a time). COMPLETABLE is never a read-time status of a CSA-routed application; it is the name of B-G9's admission
of the act. `SUSPENDED` is pending, not unable, because a suspension is not terminal (Block B §4: `ACTIVE ⇄ SUSPENDED`) — the route
may complete later — while a decision **dated** inside the suspension is refused by (iii) (probe 7a, 7b). That split is the
consequential reading needed to make the two statuses exhaustive; it introduces no new rule (HD-E9's term and the assignment edge
set are unchanged).

---

**⟨E2.013⟩** · trace: L05 03_FC_D4_D5_D6_CSA_READ_COORDINATES_AND_ADMISSION.md lines 64–66
· status: **PROPOSED — layer 5 (FC-D4: the exclusivity holds at every coordinate; probe D4-a) — appended to the mutual-exclusivity paragraph**

> The exclusivity holds at every `(state_at, known_at)` on the coordinate rule of §1.2; a decision row that is not yet eligible does not
> disturb it (probe D4-a: PENDING before the decision is effective, PENDING while it is effective but unrecorded, DECIDED only after
> both).

---

**⟨E2.014⟩** · trace: L05 03_FC_D4_D5_D6_CSA_READ_COORDINATES_AND_ADMISSION.md lines 70–75
· status: **PROPOSED — layer 5 (FC-D4/FC-D6: PENDING is the normal life; B-G9 admits the CSA decision under (b) as specialised; DECIDED only once the decision row is eligible at the read coordinates)**
· supersedes: L03 03_APPLICATION_AND_ENTITLEMENT.md line 94; L04 04_O4_CSA_COMPLETION.md lines 54–57

> PENDING is the normal life of an engaged application. It is **not** "held": no notice is due, nothing is re-engaged. For a CQ route
> B-G9 admits no decision while the CQ is open because the route's result does not yet exist **at the decision's coordinates**. For a
> CSA route the assignee's decision is itself the completing act: **B-G9 admits it under correction `04` §E2-Q2 (b), as the five terms
> of the CSA row specialise it for that route — the completion authority as at the engagement's `settings_basis`, never the live
> settings fold — and the first decision needs no earlier decision row: PC-08 forbids one.** Until that act is presented, the
> application's status is PENDING; **once the decision row is eligible at the read coordinates, it is DECIDED — and not before.**

---

**⟨E2.016⟩** · trace: L03 03_APPLICATION_AND_ENTITLEMENT.md line 98
· words replaced from L07 01_FA_D1_WITHDRAWAL_FINALITY.md lines 220–222
· status: **PROPOSED — layer 3 (B-G9′ re-engagement refused while PENDING) with layer 7's words (W-FIN; DECIDED behind them — the 23 Sep decision (1) and CD-13); the one-limb B-G9′ itself is layer 6 §2 as corrected by layer 7 §3 (below)**
· supersedes: L03 03_APPLICATION_AND_ENTITLEMENT.md line 98; L05 03_FC_D4_D5_D6_CSA_READ_COORDINATES_AND_ADMISSION.md lines 176–177; L06 01_AM_WITHDRAWAL_AND_REAPPLICATION.md lines 204–206

**Re-engagement is refused while PENDING.** A guardian companion to B-G9 (**B-G9′**, proposed): an engagement row of ordinal *n + 1* is admitted only if engagement *n* is UNABLE (its `basis_reference` resolves to the owner's UNABLE record) **and no withdrawal of the application is on the record at the row's record time — whatever the withdrawal's or the
row's effective time — and the application is not decided at the row's coordinates. Re-application after withdrawal is a new
application (`04_O4` §2B), linked to its withdrawn predecessor and inheriting nothing from it.**. This is what stops a settings or population change — or an impatient Coordinator — from re-routing a CQ that is merely taking time.

---

**⟨E2.018⟩** · trace: L03 03_APPLICATION_AND_ENTITLEMENT.md lines 102–108
· status: **PROPOSED — layer 3 (notices and re-engagement, checked together — rows waiting, pending, held; the 'completable' row as replaced by layer 4 follows)**

**Notices and re-engagement, checked together.**

| condition | derived from | notice (PC-24) | re-engagement |
|---|---|---|---|
| waiting (unengaged, route blocked) | no engagement row; the blocking rule | `APPLICATION_WAITING` once per condition | none — first engagement when available |
| pending (engaged, route working) | route owner's status | **none** | **refused** (B-G9′) |
| held (engaged, route unable) | route owner's UNABLE record | `APPLICATION_HELD` (LO board; Circle board for a Circle route) | required, explicit, next ordinal |

---

**⟨E2.019⟩** · trace: L04 04_O4_CSA_COMPLETION.md line 61
· status: **PROPOSED — layer 4 (O-4: the 'completable' row)**
· supersedes: L03 03_APPLICATION_AND_ENTITLEMENT.md line 109

> | completable | CQ: route owner's result exists. CSA: the assignee's act is presented and admitted by B-G9 | none | n/a — B-G9 admits the decision; the application becomes decided |

---

**⟨E2.020⟩** · trace: L07 01_FA_D1_WITHDRAWAL_FINALITY.md line 226
· status: **PROPOSED — layer 7 (the 'decided / withdrawn' row: at most one terminal row exists; a decided application admits no withdrawal — DECIDED CD-13); G-1's invariant OWED**
· supersedes: L03 03_APPLICATION_AND_ENTITLEMENT.md line 110; L05 03_FC_D4_D5_D6_CSA_READ_COORDINATES_AND_ADMISSION.md lines 179–181; L06 01_AM_WITHDRAWAL_AND_REAPPLICATION.md line 210

> | decided / withdrawn | PC-08 row / withdrawal row, **each eligible at the read coordinates; at most one exists for an application** | (domain's own) | n/a — **a withdrawn application admits no engagement and no decision once the withdrawal is on the record, whatever their effective times; a decided application admits no withdrawal; re-application is a new application, first engagement ordinal 1 (`04_O4` §2B)** |

---

**⟨E2.022⟩** · trace: L02 04_BLOCK_E2_REVISED_SECTIONS.md lines 83–92
· status: **PROPOSED — layer 2 (PF-09 (e) interfaces, owners, dependencies; status); OWED — DISCUSSION CQ lifecycle, SERVICE CSA workflow**

**(e) Interfaces, owners, dependencies.**

| what | owner | PKT-B needs |
|---|---|---|
| CQ lifecycle (opening, participant set, conclusion, result) | DISCUSSION | the CQ-opening record naming the application as subject (the `engagement_reference`), and the conclusion result; whether DISCUSSION freezes a CQ's participant set at opening is DISCUSSION's rule, and PF-09 does not depend on it |
| CSA workflow (assignment, ending) | SERVICE | PF-01's assignment record as the `engagement_reference`; its fold at completion |
| application, engagement, decision, withdrawal rows; B-G9 | PKT-B | — |
| the held-application notice | DISCUSSION (Topic) / PKT-B (evidence row) | §C-7 |

**Status (AC-6).** PF-09, B-G9, the engagement and withdrawal relations: proposed. PC-08: proposed. Nothing installed.

⟦editorial⟧ The status read and the admission test — layer 4 O-4 §1 (the two things separated), with the first row as replaced by layer 5 FC-D4 §1.1 and the one word 'resubmission' struck by layer 6 §1.1; then the eligibility rule (layer 5 §1.2):

---

**⟨E2.023⟩** · trace: L04 04_O4_CSA_COMPLETION.md lines 23–26
· status: **PROPOSED — layer 4 (O-4 §1 heading and table header)**

## 1. The two things, separated

| thing | asked of | when | values |
|---|---|---|---|

---

**⟨E2.024⟩** · trace: L05 03_FC_D4_D5_D6_CSA_READ_COORDINATES_AND_ADMISSION.md line 46
· words replaced from L06 01_AM_WITHDRAWAL_AND_REAPPLICATION.md lines 77–78
· status: **PROPOSED — layer 5 (FC-D4 §1.1: the route status read at coordinates) with layer 6's word change ('resubmission' struck; 'the application record itself' added)**
· supersedes: L04 04_O4_CSA_COMPLETION.md line 27; L05 03_FC_D4_D5_D6_CSA_READ_COORDINATES_AND_ADMISSION.md line 46
· note ⟦editorial⟧: Layer 6 §1.1 states the corrected list in prose; the substituted words are the layer-6 list (L6 01_AM lines 77–78) minus its surrounding sentence.

> | **the route status of an engaged application** | the application, at read coordinates `(state_at, known_at)`, **with no candidate act on the table**; **every row the status reads — the application record itself, decision, withdrawal, engagement,
route closure, the assignment chain, DISCUSSION's CQ result — is read only if eligible at those coordinates (§1.2)** | whenever CARE, PF-09's notices or B-G9′ ask "where is this application?" | `PENDING` / `UNABLE` (CSA); `PENDING` / `COMPLETABLE` / `UNABLE` (CQ) — plus the row-derived `WAITING`, `DECIDED`, `WITHDRAWN` outside the route, **each at the same coordinates** |

---

**⟨E2.025⟩** · trace: L04 04_O4_CSA_COMPLETION.md line 28
· status: **PROPOSED — layer 4 (O-4 §1 second row: the admission of the assignee's act — B-G9 at its own coordinates)**

| **the admission of the assignee's act** | a candidate `core_applicationdecision` row `NEW`, by guardian **B-G9**, at its own coordinates `(NEW.effective_at, NEW.recorded_at)` | when the assignee decides | admitted / refused |

---

**⟨E2.026⟩** · trace: L05 03_FC_D4_D5_D6_CSA_READ_COORDINATES_AND_ADMISSION.md lines 50–54
· status: **PROPOSED — layer 5 (FC-D4 §1.2 — one eligibility rule for every row the status reads: effective_at ≤ state_at and recorded_at ≤ known_at; a later recording never rewrites an earlier read); DECIDED behind it — CD-2 (AT-04 names FC-D4's rule)**

> **One rule for every row the status reads.** A row is *eligible at `(state_at, known_at)`* iff `effective_at ≤ state_at` **and**
> `recorded_at ≤ known_at` (v0.13 §5.2; `received_at ≤ recorded_at` by §5.3, so `received_at ≤ known_at` follows). A row not
> eligible at the read coordinates is not there for that read — a decision effective at 50 and recorded at 60 does not exist for a
> read at (30, 30), nor at (55, 55), nor at (70, 55); it exists at (55, 70) and after. **A later recording never rewrites an earlier
> read.** The rule is the assignment fold's rule (already applied to PENDING/UNABLE) applied to every other row the status names.

---

**⟨E2.027⟩** · trace: L04 04_O4_CSA_COMPLETION.md lines 30–36
· status: **PROPOSED — layer 4 (O-4 §1 prose: for a CSA route the assignee's decision is the completing act; COMPLETABLE a property of the act)**

The supplement's CSA row put both in one cell: "COMPLETABLE — the assignment folds `ACTIVE` at the decision's `effective_at` and
capacity holds" was the admission test of an act; "PENDING — the assignment folds `ACTIVE` and no decision has been recorded" was the
read-time status. An active assignment with no decision satisfied both, and the prose then said B-G9 admits only COMPLETABLE while
also admitting nothing until the assignee decides — so the first decision was never shown admitted. **For a CSA route there is no
intermediate result record**: the assignee's decision *is* the act that completes the route. So "COMPLETABLE" is a property of that
act under B-G9, not a read-time status of the application. For a CQ route the result record exists before the decision row is
written, which is why COMPLETABLE is a read-time status there and why the CQ row stands.

⟦editorial⟧ The walk — O-4 §3's seven rows (layer 4) and the three rows added by layer 5 §3, the third as replaced by layer 6 §1.2:

---

**⟨E2.028⟩** · trace: L04 04_O4_CSA_COMPLETION.md lines 63–73
· status: **PROPOSED — layer 4 (O-4 §3 walk rows: first authorised decision; non-assignee; expired or ended assignment; duplicate; re-engagement while PENDING; after UNABLE; during a suspension); LIMIT — probe cases, reference check only**

## 3. The walk — the first authorised decision, and the refusals retained (probe cases)

| case | rows before | `NEW` | B-G9 | probe |
|---|---|---|---|---|
| **first authorised decision** | engagement 1 (`LO_CSA`, assignment A1 held by the assignee, `ACTIVE` from 12); **no decision row** | by the assignee, `effective_at = 40`, `recorded_at = 40` | (i) ✓ (ii) ✓ (iii) `ACTIVE` at (40, 40) ✓ (iv) ✓ (v) none exists ✓ → **admitted**; status before: PENDING; after: decided | 1, 2, 2b |
| non-assignee | as above, engagement on assignment A2 held by another | enterer does not hold A2 | (ii) fails → refused | 4 |
| expired or ended assignment | A2 `ENDED` at 50, no decision | `effective_at = 60` | (iii) folds `ENDED` → refused; read status at (60, 60): **UNABLE** | 6a, 6b |
| duplicate decision | a decision row exists | a second row for the application | (v) fails → refused (`unique_violation`; a byte-identical redelivery is a replay under Block C §3.2's comparison) | 3 |
| re-engagement while an assignee's act is awaited | engagement 1 PENDING | engagement row of ordinal 2 | **B-G9′** refuses: engagement *n* is PENDING | 5 |
| re-engagement after UNABLE | engagement 1 UNABLE (A2 `ENDED`) | engagement 2, `basis_reference` naming the UNABLE record | B-G9′ admits; the new assignee's first decision then admitted by B-G9 | 6c, 6d |
| decision dated during a suspension | A3 `SUSPENDED` at 30 | `effective_at = 35` | (iii) folds `SUSPENDED` → refused; read status PENDING | 7a, 7b |

---

**⟨E2.029⟩** · trace: L05 03_FC_D4_D5_D6_CSA_READ_COORDINATES_AND_ADMISSION.md lines 79–80
· status: **PROPOSED — layer 5 (FC-D4 row: decision read before effective/recorded/after both; FC-D6 row: settings change after engagement — the live fold never read)**

> | **decision read before it is effective, before it is recorded, and after both** (FC-D4) | engagement 1 PENDING; a decision **effective at 50, recorded at 60** | reads at (30, 30), (55, 55), (70, 55), (55, 70), (70, 70) | the status is **PENDING, PENDING, PENDING, DECIDED, DECIDED** — never DECIDED before both coordinates are met | D4-a |
> | **settings change after engagement** (FC-D6) | engagement 1 with `settings_basis` = route enabled; the live settings then disable the route at 25 | the assignee's decision at (40, 40) | (b) reads `settings_basis` → **admitted**; the converse (basis disabled, live enabled) → **refused**. The live fold is never read | D6-a, D6-b |

---

**⟨E2.030⟩** · trace: L06 01_AM_WITHDRAWAL_AND_REAPPLICATION.md line 83
· status: **PROPOSED — layer 6 (FC-D5 row as amended: re-engagement refused on the two counterexamples; re-application is a new application)**
· supersedes: L05 03_FC_D4_D5_D6_CSA_READ_COORDINATES_AND_ADMISSION.md line 81

> | **re-engagement refused on the two counterexamples** (FC-D5) | (i) a CQ concluded with a result, no decision; (ii) a withdrawn application | an engagement row of ordinal 2 | **B-G9′ refuses both**: the one limb does not hold — (i) is completed by B-G9 admitting the decision, not by a new engagement; (ii) a withdrawn application admits no engagement; **re-application is a new application (§2B)** | D5-i, D5-ii |

---

**⟨E2.031⟩** · trace: L05 03_FC_D4_D5_D6_CSA_READ_COORDINATES_AND_ADMISSION.md lines 85–102
· status: **PROPOSED — layer 5 (FC-D4 §4: the CQ distinction preserved at read coordinates; the interface requirement on DISCUSSION — cq_status as of coordinates — OWED, unimplemented (EL-6); fail closed on CQ routes if it cannot be supplied)**
· supersedes: L04 04_O4_CSA_COMPLETION.md lines 75–80

> ## 4. The CQ distinction — preserved, at read coordinates
>
> CQ rows unchanged in value: DISCUSSION reports **open** → PENDING (a progressing CQ is not held; re-engagement refused by B-G9′);
> **concluded with a result** → COMPLETABLE (B-G9 admits the decision that names it as `supporting_result_reference`); **concluded
> without a result / unable to conclude** → UNABLE (held; `APPLICATION_HELD` through PC-24 under O-3's destination identity; explicit
> re-engagement of the next ordinal). **What changes is *when* each value is read (FC-D4): the status is asked at `(state_at,
> known_at)`, and DISCUSSION's answer must be as of those coordinates.**
>
> **Interface requirement on DISCUSSION (owner: DISCUSSION; proposed, not implemented).** DISCUSSION's conclusion of a CQ is a record
> under its own four-time grammar. It **carries** `effective_at` (when the conclusion takes effect — the result's date) and
> `recorded_at` (when DISCUSSION recorded it); PKT-B **reads both**: the result is eligible for a read at `(state_at, known_at)` iff
> `effective_at ≤ state_at` and `recorded_at ≤ known_at`. DISCUSSION must therefore expose its three-valued status **as of
> coordinates** — `cq_status(engagement_reference, state_at, known_at)` — returning *open* wherever the conclusion is not yet eligible,
> and the result record's reference only where it is (probe D4-d: PENDING at (30, 30), (55, 55) and (70, 55); COMPLETABLE at
> (55, 70) and (70, 70)). A decision that names a result not eligible at the decision's own coordinates is refused by B-G9 (D4-e).
> The (e) dependency on DISCUSSION's three-valued status stands, **widened by this coordinate requirement**: if DISCUSSION can supply
> the values but not as of coordinates, PKT-B cannot read CQ status bitemporally and must fail closed on CQ routes; stated as a
> dependency, not a default.

⟦editorial⟧ §2A — Read coordinates: the rule for every status (layer 5 FC-D4 §5), with step 0 inserted by layer 6, steps 1–2 replaced by one row by layer 7 (W-FIN) whose double-row text is replaced by layer 9 RT-36 → layer 10 RT10-06 → layer 13 RT13-01 (CD-14; ruling A), step 3 replaced by layer 6, and step 3a added by MP-1 (layer 9):

---

**⟨E2.032⟩** · trace: L05 03_FC_D4_D5_D6_CSA_READ_COORDINATES_AND_ADMISSION.md lines 104–110
· status: **PROPOSED — layer 5 (§2A heading, rule and table header)**

## 5. New §2A — Read coordinates: the rule for every status (FC-D4)

The route status of an application at `(state_at, known_at)` is derived in this order, each step reading only rows eligible at the
coordinates (§1.2):

| step | reads | yields |
|---|---|---|

---

**⟨E2.033⟩** · trace: L06 01_AM_WITHDRAWAL_AND_REAPPLICATION.md line 89
· status: **PROPOSED — layer 6 (§2A step 0: the application record eligible at the coordinates, else NOT_PRESENT)**

> | 0 | the application record (PC-08's subject) eligible at the coordinates | not eligible → **NOT_PRESENT** — stop (the application does not exist for that read; a record is read under the same rule as every row it owns) |

⟦editorial⟧ Steps 1–2 as one row — layer 7 §1 (W-FIN), the double-row text within it as replaced by RT-36 (layer 9, CD-14), whose text is replaced in full by RT10-06 (layer 10), whose two spans are replaced by RT13-01 (a) and (b) (layer 13, ruling A). Rendered: the layer-7 row with its double-row sentence replaced by the layer-10 text as amended by layer 13:

---

**⟨E2.034⟩** · trace: L07 01_FA_D1_WITHDRAWAL_FINALITY.md line 85
· words replaced from L10 01_REPLACEMENTS_AND_ADDITIONS_L10.md line 51
· words replaced from L13 02_REPLACEMENTS_AND_ADDITIONS_L13.md line 35
· words replaced from L13 02_REPLACEMENTS_AND_ADDITIONS_L13.md line 40
· words replaced from L14 01_REPLACEMENTS_AND_ADDITIONS_L14.md line 417 (RT14-26, A1-D05)
· words replaced from L14 01_REPLACEMENTS_AND_ADDITIONS_L14.md line 431 (RT14-27, A1-D05)
· words replaced from L14 01_REPLACEMENTS_AND_ADDITIONS_L14.md line 445 (RT14-28, A1-D05)
· words replaced from L14 01_REPLACEMENTS_AND_ADDITIONS_L14.md line 459 (RT14-29, A1-D05)
· words replaced from L14 01_REPLACEMENTS_AND_ADDITIONS_L14.md line 473 (RT14-30, A1-D05)
· status: **DECIDED — CD-13, CD-14 option A, ruling A with qualifications, as before; PROPOSED — layer 13's EXPRESSION of A as corrected by layer 14 (the validity test requires an operative correction; branches (i)/(ii) test recorded_at and effective_at against known_at; the composition note's DQ-25 reference); MP-3 OPEN; OWED — G-1, G-2; LIMIT — RC_P5 and layer 14 family V sequential** — layer 14 (US015-B-5 integration)
· status at candidate v1.2 (preserved; policies it names DECIDED remain so): **DECIDED — CD-13 (W-FIN: at most one terminal row; the first admitted stands), CD-14 option A (the integrity refusal — a present refusal, no status served while the conflict is unresolved; not a platform shutdown), the historical-view ruling A with qualifications (24 Sep 2026 — verbatim beside this paragraph, below); PROPOSED — layer 13's EXPRESSION of A; MP-3 (the correction's authority, grounds, mechanism) OPEN; OWED — G-1, G-2 (concurrency), enforcement evidence; LIMIT — RC_P5 sequential, stated cases only (EL-15, EL-16); RD-1 and the clock closed on inspected text only**
· layer 14 displaces: L13 02_REPLACEMENTS_AND_ADDITIONS_L13.md lines 35 and 40 (RT13-01's post-correction text within L07 01_FA_D1_WITHDRAWAL_FINALITY.md line 85 as replaced by L10 line 51) — the five spans quoted at RT14-26…30
· supersedes: L05 03_FC_D4_D5_D6_CSA_READ_COORDINATES_AND_ADMISSION.md lines 111–112; L06 01_AM_WITHDRAWAL_AND_REAPPLICATION.md line 90; L07 01_FA_D1_WITHDRAWAL_FINALITY.md line 85; L09 02_REPLACEMENTS_AND_ADDITIONS.md line 274; L10 01_REPLACEMENTS_AND_ADDITIONS_L10.md line 51

> | 1–2 | **the terminal row.** By W-FIN (§2) at most one of a `core_applicationdecision` row and a `core_applicationwithdrawal` row exists for an application. If the decision row exists and is eligible at the coordinates → **DECIDED** — stop. If the withdrawal row exists and is eligible → **WITHDRAWN** — stop. **There is no later step for a withdrawn application: re-application is a new application record (§2B §3) with its own status.** **If more than one terminal row is stored for this application, the read first tests the store as it is now, past eligibility (CD-14, option A): while no authorised correction act (MP-3) stored now names exactly one of those rows as standing, no status is served for this application at any date at which the application is on record — every such read reports an INTEGRITY FAILURE, not a status. The refusal is a present fact about the store's validity, not a claim that the conflict was known at the earlier date: a view whose `known_at` predates one of the rows is refused all the same, because no view is served from an invalid store. Step order never chooses between the rows; no other application is affected (this is not a platform shutdown); the underlying rows remain readable as rows within the access rules; CARE's explanation and the authorised resolution route are MP-3; no automatic repair is promised** (W14). **Once a correction act stored now and operative now — its `effective_at` at or before `now`, §5.3 (layer 14, A1-D05) — names exactly one standing terminal row, the store is valid again and views are served — each by its own knowledge time (the Human Governor's ruling A, 24 September 2026; modelled at RC_P5, layer 13). (i) *Before the correction is eligible and operative at the view's knowledge time* — its `recorded_at` or its `effective_at` after the view's `known_at` — the view uses the records then eligible under §5.2 and §5.3: where only one terminal row had been recorded by `known_at`, that row's status by its own eligibility; where more than one terminal row had been recorded by `known_at` — the conflict then on the record and unresolved — the historical INTEGRITY FAILURE, reported as what was then on the record. (ii) *Once the correction is eligible and operative at the view's knowledge time* — its `recorded_at` and its `effective_at` both on or before `known_at`; a correction known but not yet operative at `known_at` is historical-but-not-current (§5.3) and the view is served under (i) — the corrected account: the standing row alone, subject to its own ordinary eligibility (`effective_at` against `state_at`; `recorded_at` against `known_at`), the excluded row yielding no status at that view; where the standing row is not yet effective at `state_at`, the view is the pre-terminal status as corrected. The excluded row stays on the record as a row and as the subject of the correction. CARE serves every view after a correction with its explanation (AT13-02): the account the view shows — knowledge-time account, historical refusal or corrected account — and, separately, a present-day annotation identifying the correction, the displaced record, its replacement and the correction's authority as recorded. Historical display revives no ended membership and confers no current authority: a view is a read at its coordinates, composes no transition on any chain, and current state and authority are served at `(now, now)` only. While the conflict remains unresolved now — no correction stored, or a stored correction not yet operative (layer 14) — the refusal above continues unchanged, and selecting an earlier knowledge time does not bypass it.** **Composition with the temporal-reading rules, stated for A1:** §5.2's eligibility test and §5.3's not-yet-recorded rule govern what enters a *view*; the validity test is a condition on whether any view is served at all, applied to the present store; the correction's selection of the record set enters each view by that view's knowledge time — the correction's `recorded_at` against `known_at` (§5.3 orders `received_at ≤ recorded_at`, so `recorded_at ≤ known_at` satisfies both of §5.2's knowledge tests; the correction's `effective_at` selects no record set by `state_at` but decides, against `known_at`, whether the correction is operative at that view — DQ-25 as corrected by layer 14) — so the refusal engages neither clause, and the correction enters no view as a status event: at each knowledge time at which it is eligible, it fixes the record set the view is computed over. Layer 9's MP-3 sentence ("historical reads before that time continue to refuse", modelled by RC_P1 P8's `hist_known70`) and layer 10's post-correction sentence (every view from the standing row alone — RC_P2 I2, I3) are both history: the ruling coincides with the former only at coordinates inside the conflict interval and with the latter only at coordinates at which the correction is eligible, and neither is the rule — the ruling's words are. A1 should test the ruling's composition against §5.2 and §5.3. G-1's exclusion under concurrency (MP-12) remains owed. Neither row eligible → continue |

⟦editorial⟧ RULING A — the Human Governor's verbatim words (capture 01_HUMAN/HG_RULING_HISTORICAL_VIEW_A_20260924_CAPTURE.txt, line 11), placed beside the status-read provisions it governs:

---

**⟨E2.035⟩** · trace: 01_HUMAN/HG_RULING_HISTORICAL_VIEW_A_20260924_CAPTURE.txt line 11 (carrier member, sha256 6513b3c7…f98)
· status: **DECIDED — ruling A with qualifications (Carmian Owen, Human Governor, 24 Sep 2026, verbatim). Authority boundary (same capture, line 17): it governs historical status views after a lawful correction; it does not authorise choosing a later withdrawal over an earlier admitted acceptance merely because the records conflict; CD-13 stands; the correction's authority, grounds and mechanism remain to be specified and assessed. layer 13 expresses A, and RC_P5 models its cases (layer 13 EL-16); A is not implemented; capture line 19 directed that A not be described as already modelled or implemented.**

> After an authorised correction resolves conflicting application terminal records, historical status views preserve the distinction between what was known then and the corrected account now. A view whose knowledge time precedes the correction uses the records then eligible, including an integrity refusal where the conflict was then known and unresolved. Once the correction is eligible at the view’s knowledge time, the corrected account applies, subject to the ordinary temporal eligibility rules. CARE identifies the correction, the displaced record, its replacement and its authority, and clearly distinguishes any present-day annotation from the historical account. Historical display does not revive an ended membership or confer current authority. While the conflict remains unresolved, CD-14’s refusal continues unchanged.

---

**⟨E2.036⟩** · trace: L13 02_REPLACEMENTS_AND_ADDITIONS_L13.md line 87
· words replaced from L14 01_REPLACEMENTS_AND_ADDITIONS_L14.md line 487 (RT14-31, A1-D05)
· words replaced from L14 01_REPLACEMENTS_AND_ADDITIONS_L14.md line 501 (RT14-32, A1-D05)
· status: **PROPOSED — layer 13 as corrected by layer 14 (the annotation names the operative time where later than the record time; the not-yet-operative case stated as a present fact); DECIDED behind it — ruling A, CD-2, CD-14; LIMIT — field shape modelled at RC_P5 E/T4 and layer 14 V only** — layer 14 (US015-B-5 integration)
· status at candidate v1.2 (preserved; policies it names DECIDED remain so): **PROPOSED — layer 13 (CARE's explanation contract for status views after a correction — the account and the present-day annotation, never merged); DECIDED behind it — ruling A, CD-2, CD-14; LIMIT — field shape modelled at RC_P5 families E, T4 only**
· layer 14 displaces: L13 02_REPLACEMENTS_AND_ADDITIONS_L13.md line 87 (AT13-02) — the two spans quoted at RT14-31/32

**CARE's explanation contract for status views after a correction (ruling A; CD-2's accepted CARE requirement; CD-14's).** Every status view served for an application on which a correction act is stored now carries an explanation in two parts that are never merged: **the account** — what this view shows and on which records, one of *knowledge-time account* (one terminal row then recorded), *historical refusal* (the conflict then on the record and unresolved: the rows then known, and why no reliable status was then servable), or *corrected account* (the standing row, the correction eligible at this knowledge time, the excluded row yielding no status) — and **the present-day annotation** — what is true now about those records: the correction (its `recorded_at`; its `effective_at` where later than its `recorded_at`, as the time from which it is operative — layer 14; its authority *as recorded*, identified as distinct from the authority for the original act and not assessed by CARE), the displaced record, its replacement, the current status as of now, and the two limits stated plainly: historical display revives no ended membership, and it confers no current authority — current state is served at `(now, now)`. The annotation never alters the account: a knowledge-time account still shows the status then on the record; a historical refusal still refuses. Where the correction record carries grounds, source or evidence, CARE states them as recorded and within the access rules, distinguishing fact from inference and not claiming that every consequence is known (CD-2); CARE never judges the original act or the correction and never chooses between records. While the conflict is unresolved now, the explanation is CD-14's — the conflict, why no reliable status can be served, that this is a present integrity refusal and not a claim the conflict was known at the coordinates asked for, what can be done and who can resolve it (MP-3, a proposal) — and carries no annotation of a corrected account; where a correction is stored but not yet operative, the explanation states its recorded existence and the time from which it will be operative, as a present fact that alters no account (layer 14). Field shape modelled at RC_P5 families E and T4; no prose, notice posting, access-rule filtering or interface is modelled or claimed. Who may authorise a correction, its grounds and its mechanism are not specified here.

---

**⟨E2.037⟩** · trace: L07 01_FA_D1_WITHDRAWAL_FINALITY.md line 87
· status: **PROPOSED — layer 7 (step 3 stands: the engagement rows eligible; none → WAITING; the current engagement)**
· supersedes: L06 01_AM_WITHDRAWAL_AND_REAPPLICATION.md line 91; L05 03_FC_D4_D5_D6_CSA_READ_COORDINATES_AND_ADMISSION.md line 113

Step 3 (01_AM line 91) stands: the engagement rows eligible at the coordinates; none → WAITING; otherwise the current engagement.

⟦editorial⟧ Step 3a (PAUSED) — added by MP-1 (layer 9, PROPOSED), read after step 3 and before step 4; MP-1's full text is placed at the end of this section.

---

**⟨E2.038⟩** · trace: L05 03_FC_D4_D5_D6_CSA_READ_COORDINATES_AND_ADMISSION.md lines 114–116
· status: **PROPOSED — layer 5 (§2A steps 4–6: route closure → UNABLE; CQ route → DISCUSSION's status as of the coordinates; CSA route → the assignment fold)**

| 4 | a route-closure fact for that engagement (the deciding Circle `CLOSED`; the LO dissolved), eligible | **UNABLE** — stop |
| 5 | for a CQ route: DISCUSSION's status as of the coordinates (§4) | open → **PENDING**; result → **COMPLETABLE**; no result / unable → **UNABLE** |
| 6 | for a CSA route: the assignment named by `engagement_reference`, folded at the coordinates | terminal → **UNABLE**; non-terminal → **PENDING** |

---

**⟨E2.039⟩** · trace: L07 01_FA_D1_WITHDRAWAL_FINALITY.md lines 89–105
· status: **PROPOSED — layer 7 (what the corrected read produces for the Reviewer's sequence — W9′; the (54, 60) read); LIMIT — RD-1 and the clock closed on inspected text only (Reviewer, 24 Sep 2026)**

**What the corrected read produces for the Reviewer's sequence** (W9′; FA_AF1 §A "corrected"; the withdrawal effective 55 / recorded 58
admitted, the successor admitted at 59, the decision effective 50 / recorded 60 refused):

| read | appR | appR2 |
|---|---|---|
| (58, 58) | WITHDRAWN | NOT_PRESENT |
| (59, 59) | WITHDRAWN | WAITING |
| (54, 60) | PENDING (engagement 1 eligible; the withdrawal, effective 55, not yet) | NOT_PRESENT |
| **(55, 60)** | **WITHDRAWN** | NOT_PRESENT |
| **(60, 60)** | **WITHDRAWN** | WAITING |
| (1000, 1000) | WITHDRAWN | WAITING |
| grid `state_at` 0..79 × `known_at` {60, 70, 1000}, 240 cells | DECIDED **nowhere**; NOT_PRESENT 0..4; WAITING 5..19; PENDING 20..54; WITHDRAWN 55..79 — identical at every `known_at ≥ 60` | — |
| (70, 70) after the successor's own engagement and ACCEPT | WITHDRAWN | DECIDED |

The read at (54, 60) is PENDING, not WITHDRAWN, because the withdrawal is effective at 55: a read before its effective time shows the
application as it stood then. That is FC-D4's eligibility rule for the withdrawal row, unchanged (D4-b). W-FIN changes what may be
*admitted* after the withdrawal is on the record, not when the withdrawal is *read*.

---

**⟨E2.040⟩** · trace: L05 03_FC_D4_D5_D6_CSA_READ_COORDINATES_AND_ADMISSION.md lines 118–121
· status: **PROPOSED — layer 5 (§2A demonstrated for each status before effectiveness, before recording, after both — D4-a…f); the D5-v clause (line 122) as replaced by layer 6 follows**

Demonstrated for each status before effectiveness, before recording, and after both (each fact effective at 50, recorded at 60; reads
at (30, 30), (55, 55), (70, 55), (55, 70), (70, 70)): decision → PENDING ×3 then DECIDED ×2 (D4-a; the case "(30, 30) must not
return DECIDED" holds); withdrawal → PENDING ×3 then WITHDRAWN ×2 (D4-b); the selection of engagement 2 → engagement 1's UNABLE ×3
then engagement 2's PENDING ×2 (D4-c); DISCUSSION's result → PENDING ×3 then COMPLETABLE ×2 (D4-d); route closure → PENDING ×3 then

---

**⟨E2.041⟩** · trace: L06 01_AM_WITHDRAWAL_AND_REAPPLICATION.md lines 93–95
· status: **PROPOSED — layer 6 (the replaced D5-v clause: a new application linked to a withdrawn predecessor — W1-b, W2-b)**
· supersedes: L05 03_FC_D4_D5_D6_CSA_READ_COORDINATES_AND_ADMISSION.md line 122

Line 122's clause "resubmission (effective 40, recorded 45) → WITHDRAWN ×3 then WAITING ×2 (D5-v)" is replaced by: "**a new application
(effective 40, recorded 40) linked to a withdrawn predecessor → the predecessor WITHDRAWN at every later read; the new application
NOT_PRESENT at (35, 35) and (40, 39), WAITING at (40, 40) and after (W1-b, W2-b)**".

⟦editorial⟧ 'Admission reads its own coordinates the same way' — layer 5 §5's paragraph replaced by layer 6 §1.4, replaced by layer 7 §2 (W-FIN), whose W-FIN paragraph (L7 01 lines 113–123) is replaced by layer 8 R1-e (with C-c), whose 'Second half' sentence is replaced by layer 9 RT-35a (CD-13):

---

**⟨E2.042⟩** · trace: L07 01_FA_D1_WITHDRAWAL_FINALITY.md lines 109–111
· status: **PROPOSED — layer 7 (admission reads its own coordinates, with one exception the Human decision requires — the eligible ones; D4-e)**
· supersedes: L05 03_FC_D4_D5_D6_CSA_READ_COORDINATES_AND_ADMISSION.md lines 124–127; L06 01_AM_WITHDRAWAL_AND_REAPPLICATION.md lines 99–103

> **Admission reads its own coordinates the same way, with one exception the Human decision requires.** B-G9 evaluates `NEW` at
> `(NEW.effective_at, NEW.recorded_at)`: the application record, the current engagement, the assignment fold and DISCUSSION's result are
> the eligible ones there; a decision naming a CQ result effective after the decision is refused (D4-e).

---

**⟨E2.043⟩** · trace: L08 01_RD1_RD2_RD3_REPLACEMENTS.md lines 86–90
· words replaced from L09 02_REPLACEMENTS_AND_ADDITIONS.md line 254
· status: **DECIDED — the 23 Sep decision (1) at the point of admission (W-FIN first half — held only as quoted in the amendment commission, register B); CD-13 option A (the mirror order W10 — the first accepted terminal outcome stands; a later withdrawal after a recorded decision refused whatever its date); LIMIT — 'on the record at T' is a timestamp comparison; the per-application record clock is a MODEL ASSUMPTION, not a §5.3 guarantee (the clock finding, closed on inspected text only); OWED — G-1, G-2**
· supersedes: L07 01_FA_D1_WITHDRAWAL_FINALITY.md lines 113–123; L08 01_RD1_RD2_RD3_REPLACEMENTS.md line 90
· note ⟦editorial⟧: The layer-8 replacement is carried as a blockquote ('> ' prefixed) in its source and is rendered with that carrier retained.

> **W-FIN — the withdrawal is final on the record.** *Throughout this rule, "on the record at T" means a row whose `recorded_at ≤ T` among the rows stored for the application — a timestamp comparison over stored rows. In the reference model a per-application record clock, which is a model assumption (§10.2, §10.4), makes that comparison coincide with the row's existence at insert time and with the order of admission; v0.13 §5.3 does not establish that coincidence (§10.4).*
>
> **First half — the issued decision of 23 September 2026 at the point of admission** ("the withdrawn application stays withdrawn"; "a withdrawn application admits no engagement and no decision — including a decision under an engagement created before the withdrawal"). Once a `core_applicationwithdrawal` row `W` is on the application's record at `NEW.recorded_at` — `W.recorded_at ≤ NEW.recorded_at` — **no decision row and no engagement row is admitted for that application, whatever `NEW.effective_at` is.** The withdrawal's own `effective_at` plays no part in the refusal: a decision or engagement whose effective time precedes the withdrawal's is refused exactly as one whose effective time follows it (W9′, W13-a, W13-b; the eligible-at-coordinates cases W1, D5-ii, D5-ii-b, D5-ii-c stand).
>
> **Second half — the mirror recording order (W10), decided by the Human Governor (CD-13, 24 Sep 2026, option A): "Once an application decision is recorded, a later application withdrawal is refused regardless of its supplied effective date. Of decision and withdrawal, the first accepted by the system stands and the conflicting later terminal write is refused."** A `core_applicationdecision` row on the record at `W.recorded_at` excludes the later-recorded withdrawal whatever the withdrawal's effective time (§7.1; W5-a, W10, W11). The issued sentences ("each undecided application may be withdrawn"; "remains withdrawn") do not define *undecided*; the definition this half applies — no decision row on the record at the withdrawal's record time, whatever either effective time — is this seat's, proposed for the design reason §4 gives, and neither it nor its opposite is a Human ruling. Under both halves the application's two terminal acts are mutually exclusive on the record: the row with the earlier `recorded_at` excludes the other, and at equal `recorded_at` the row already written — an admission-order test at equality (W11, W12). **B-G9′ and B-G10 read their own coordinates the same way for every other term** (§2B). **The predecessor link is read by none of them** (§2B §3.3).

---

**⟨E2.044⟩** · trace: L07 01_FA_D1_WITHDRAWAL_FINALITY.md lines 125–126
· status: **PROPOSED — layer 7 (W-FIN is stated for these record kinds only; selects nothing in LD-D1 — LD-D1's principle DECIDED (CD-2), its residue OPEN)**

> W-FIN is stated for these record kinds only. It is not a rule of record order or effective order for any other kind, does not say
> what happens to a term whose premises later fail, and selects nothing in `LD-D1` (§10.3, the boundary).

⟦editorial⟧ §2B part 1 — B-G9′: the one limb (layer 6 §2.1), term (a) as replaced by layer 7 §3.1 and the paragraph below the table as replaced by layer 7 §3.2:

---

**⟨E2.045⟩** · trace: L06 01_AM_WITHDRAWAL_AND_REAPPLICATION.md lines 107–113
· status: **PROPOSED — layer 6 (B-G9′ heading, rule and table header)**
· supersedes: L05 03_FC_D4_D5_D6_CSA_READ_COORDINATES_AND_ADMISSION.md lines 129–143

### 2.1 The guardian

An engagement row `E` of ordinal *n + 1* is admitted, at `(E.effective_at, E.recorded_at)`, **only if all of the following hold at those
coordinates**:

| term | condition | `E.basis_reference` names |
|---|---|---|

---

**⟨E2.046⟩** · trace: L07 01_FA_D1_WITHDRAWAL_FINALITY.md line 132
· status: **PROPOSED — layer 7 (B-G9′ term (a): no withdrawal on the record at E.recorded_at — W-FIN; no decision eligible); DECIDED behind it — the 23 Sep decision (1), CD-13**
· supersedes: L06 01_AM_WITHDRAWAL_AND_REAPPLICATION.md line 114

> | (a) the application | the application record is eligible; **no `core_applicationwithdrawal` row is on the record at `E.recorded_at` (W-FIN — whatever the withdrawal's or the engagement's effective time)**; **no `core_applicationdecision` row is eligible at the coordinates** — the application is neither withdrawn nor decided | — |

---

**⟨E2.047⟩** · trace: L06 01_AM_WITHDRAWAL_AND_REAPPLICATION.md lines 115–116
· status: **PROPOSED — layer 6 (B-G9′ terms (b) the ordinal; (c) the one limb — UNABLE); B-G9′ also refuses re-engagement while PAUSED (MP-1, RT11-05)**

| (b) the ordinal | *n* is the current engagement's ordinal (§2A step 3) | — |
| (c) **the one limb — UNABLE** | the application's status at `E`'s coordinates (§2A) is **UNABLE** | the owner's UNABLE record: DISCUSSION's for a CQ route; the assignment's terminal transition or the route-closure fact for a CSA route |

---

**⟨E2.048⟩** · trace: L07 01_FA_D1_WITHDRAWAL_FINALITY.md lines 136–142
· status: **PROPOSED — layer 7 (everything else is refused — including an engagement recorded after the withdrawal with an earlier effective time; there is no second limb)**
· supersedes: L06 01_AM_WITHDRAWAL_AND_REAPPLICATION.md lines 118–122

> **Everything else is refused.** In particular: a CQ concluded with a result and no decision row (status COMPLETABLE; D5-i); a withdrawn
> application, with or without a `basis_reference` (D5-ii, D5-ii-b, W1) — **including an engagement recorded after the withdrawal with an
> earlier effective time, ordinal 1 on an unengaged application (W13-a) or ordinal 2 with the UNABLE basis on an engaged one (W13-b)**;
> a decided application (W3-h); PENDING (5, 8b, W3-d). **There is no second limb.** "Withdrawn and resubmitted" is not a state of an
> application: a withdrawn application stays withdrawn (W1-b, W9′), and re-application is a new application whose first engagement is
> ordinal 1, where B-G9′ does not apply (W3-c). A `basis_reference` naming the new application's predecessor is not a basis B-G9′ reads
> (W3-b, W3-d, W3-h).

---

**⟨E2.049⟩** · trace: L06 01_AM_WITHDRAWAL_AND_REAPPLICATION.md lines 124–126
· status: **PROPOSED — layer 6 (the FC-D probe withdrawn as a model of B-G9′; the retained refusals reproduced — AM_W1); LIMIT — reference models, not enforcement**

**The FC-D probe's `admit_engagement` is withdrawn as a model of B-G9′** (its second limb, and its failure to read the decision row —
failure (b), §7). The retained refusals are reproduced (`AM_W1`: 5, 8b, D5-i, D5-ii, D5-ii-b); the UNABLE re-engagement within a
continuing application is preserved (6c, D4-c, W5-e).

⟦editorial⟧ §2B part 2 — re-application is a new application (layer 6 §3; DECIDED behind it — the 23 Sep decision (1)), with rows of §3.3 as replaced by layer 7 §5 and §3.4 as replaced by layer 7 §6:

---

**⟨E2.050⟩** · trace: L06 01_AM_WITHDRAWAL_AND_REAPPLICATION.md lines 128–155
· status: **PROPOSED — layer 6 (§3.1 the record — predecessor_reference NULL; core_applicationresubmission removed; §3.2 what the new application must satisfy afresh — B-G2, PF-03, PC-13, the predecessor terms (i)–(v); its own route and settings basis); DECIDED behind it — the 23 Sep decision (1); AF-3 (2) OPEN (owner and audience of an application record)**
· supersedes: L05 03_FC_D4_D5_D6_CSA_READ_COORDINATES_AND_ADMISSION.md lines 145–172

## 3. New §2B, part 2 — re-application is a new application (replaces FC-D §6.2, lines 145–172)

### 3.1 The record

**The application record (PC-08's subject; correction `04` §E2-Q2 (a), row "the application record") gains one nullable column:
`predecessor_reference NULL → the application record`.** A re-application after withdrawal is **a new row of that relation for the same
Learner and the same Living Organism**, with its own `application_id`, its own four-time coordinates, its own PC-08 decision slot
(`UNIQUE (application_reference)` — one decision per application, so the new application's decision is its own), and
`predecessor_reference` naming the withdrawn application. **No new relation is added; `core_applicationresubmission` is removed.**

### 3.2 What the new application must satisfy — afresh, at its own coordinates

The new record is a Learner act admitted by the same checks as any application record, none of them read from the predecessor:

| check | as the standing text states it | probe |
|---|---|---|
| **B-G2 limb 1** — capacity | a registration row eligible at `(t_app, r_app)` (supp `02` R-05-3 walk 1, line 58, row 1) | W8 (fails when the account is deactivated — limb 2) |
| **B-G2 limb 2** — authority to submit | account access folds active at `r_app` (corr `02` §C-5 "submission of a new record") | W8 |
| **B-G2 limb 3** — evidence for late recording | as §5.3 | not modelled |
| **PF-03** | the LO is not `DORMANT` at `t_app` — "the LO admits no new application" (Return 1 Block E2 §2.3) | W8 |
| **PC-13** | the LO is not dissolved at `t_app` | W8 |
| **the predecessor, where named** | (i) an existing application record; (ii) of the **same applicant party**; (iii) for the **same LO**; (iv) with a `core_applicationwithdrawal` row **eligible at the new application's `(t_app, r_app)`**; (v) that withdrawal **effective at or before** `t_app`. An undecided, decided, other-Learner, other-LO or non-existent predecessor is refused | W6-a (×5), W6-b (×3) |

**Its processing route, engagement and governing settings basis are its own.** The route is fixed by its **first engagement, ordinal 1**
(PE-05; C-1/C-3's table), whose `settings_basis` is the fold of the LO/Circle settings at **that engagement's own coordinates** (correction
`04` §E2-Q2 (a)). Nothing is copied from the predecessor's engagement rows: a live settings change between the predecessor's engagement and
the new application's engagement is seen by the new engagement's basis and not by the predecessor's (W4). Where engagement is blocked, the
new application is WAITING and `APPLICATION_WAITING` applies as R-E2Q2 states it.

---

**⟨E2.051⟩** · trace: L06 01_AM_WITHDRAWAL_AND_REAPPLICATION.md lines 157–172
· status: **PROPOSED — layer 6 (§3.3 the link: a durable reference, the applicant's act, readable under the access rules; the link authorises nothing — rows 1–3); AF-3 (2) OPEN**

### 3.3 What the link is, and what it is not

**The link is a durable reference on the new application's record — the applicant's act.** It preserves context and lineage: a reader
who may read both records can traverse from the new application to its withdrawn predecessor, and further, where the predecessor itself
names one (W7: C3 → C2 → C1). **It is readable only under the applicable access rules**: the link is a component of the new record and
carries that record's audience; the predecessor's own record is reached only if the reader's access reaches it too. Which reach applies
is the ILC's access model, not decided or altered here.

**The link authorises nothing — no engagement, no admission, no completion, no membership.** Stated as a rule of the design and
demonstrated as refusals: `status()`, B-G9, B-G9′ and B-G10 **never read `predecessor_reference`**. Consequently:

| the link cannot | demonstrated by |
|---|---|
| let the predecessor's assignee decide the new application (no inherited completion authority) | W3-a (before engagement: no engagement eligible); W3-e (after engagement: the predecessor's route and assignee are not the new engagement's) |
| stand as a `basis_reference` for an engagement of ordinal > 1 | W3-b (unengaged), W3-d (PENDING), W3-h (DECIDED) — all refused |
| carry the predecessor's `settings_basis` | W4 |

---

**⟨E2.052⟩** · trace: L07 01_FA_D1_WITHDRAWAL_FINALITY.md lines 182–183
· status: **PROPOSED — layer 7 (§3.3 rows 'confer membership' and 'revive the predecessor' as amended — W9′-membership, W9′-grid)**
· supersedes: L06 01_AM_WITHDRAWAL_AND_REAPPLICATION.md lines 173–174

> | confer membership | W3-f, W3-g: membership follows the new application's **own** decision only; **the predecessor never becomes a member — a late-recorded ACCEPT on it is refused (W9′-membership, W9′-successor: `member(appR)` False before and after the successor's own ACCEPT; model reduction: an ACCEPT row exists on this application)** |
> | revive the predecessor | W1-b: WITHDRAWN at every later read; **W9′-grid: DECIDED nowhere on 240 cells after a decision recorded later than the withdrawal is refused** |

---

**⟨E2.053⟩** · trace: L06 01_AM_WITHDRAWAL_AND_REAPPLICATION.md lines 176–177
· status: **PROPOSED — layer 6 (the link creates no Learner and confers no membership)**

**It creates no Learner** (the new record's applicant is an existing registered Learner — B-G2 limb 1) **and confers no membership** (only a
PC-08 decision on the new application does, through the membership transition that names that decision).

---

**⟨E2.054⟩** · trace: L07 01_FA_D1_WITHDRAWAL_FINALITY.md lines 187–194
· status: **PROPOSED — layer 7 (§3.4 what it does not do to the withdrawn application — its decision slot stays empty at every record time after the withdrawal's; no application ever holds both — W14 fails closed); DECIDED — CD-13, CD-14**
· supersedes: L06 01_AM_WITHDRAWAL_AND_REAPPLICATION.md lines 179–182

> ### 3.4 What it does not do to the withdrawn application
>
> The withdrawn application is not edited, not reopened and not re-read as anything but WITHDRAWN at every coordinate where its withdrawal
> is eligible (W1-b, W9′-reads, W9′-grid). Its engagement rows belong to it and admit nothing further (W1, W13-a, W13-b). **Its decision
> slot stays empty at every record time after the withdrawal's: a decision recorded after the withdrawal is refused whatever its effective
> time (W-FIN, §2; W9′, W11-b, W11-c, W12).** A decision recorded *before* the withdrawal is the other case: then the application is decided
> and it is the withdrawal that is refused (W5-a, W10, W11-a, W11-d) — no application ever holds both (W14 shows the read failing closed
> if one did).

⟦editorial⟧ §2B part 3 — B-G10, the withdrawal guardian (layer 6 §4), term (c) as replaced by layer 7 §7.1 and the paragraph below as replaced by layer 7 §7.2, whose lines 205–207 are replaced by layer 8 R2-a; the temporal-scope paragraph as replaced by layer 8 C-d:

---

**⟨E2.055⟩** · trace: L06 01_AM_WITHDRAWAL_AND_REAPPLICATION.md lines 184–192
· status: **PROPOSED — layer 6 (B-G10 heading; terms (a) the application record eligible, (b) the actor is the applicant party)**

## 4. New §2B, part 3 — the withdrawal guardian (new; replaces the FC-D text "a guardian refusing a withdrawal after a decision … not modelled")

**B-G10 (proposed; seat-allocated in the B-Gn series; the content travels with the code).** A `core_applicationwithdrawal` row `W` is
admitted at `(W.effective_at, W.recorded_at)` only if, at those coordinates:

| term | condition | probe |
|---|---|---|
| (a) | the application record is eligible | W5-d |
| (b) | `W`'s actor is the applicant party (B-G2's Learner limb, as for any actor) | W5-c |

---

**⟨E2.056⟩** · trace: L07 01_FA_D1_WITHDRAWAL_FINALITY.md line 200
· status: **PROPOSED — layer 7 (B-G10 term (c): no decision row on the record at W.recorded_at — W-FIN's mirror); DECIDED — CD-13 (RT-35b: B-G10 term (c) is its guardian text; the definition of undecided is now the Human Governor's)**
· supersedes: L06 01_AM_WITHDRAWAL_AND_REAPPLICATION.md line 193

> | (c) | **no `core_applicationdecision` row is on the record at `W.recorded_at`** — a decided application cannot be withdrawn, **whatever the decision's or the withdrawal's effective time** (W-FIN's mirror, §2; §4) | W5-a (fourth case), **W10, W11-a, W11-d, W12** |

---

**⟨E2.057⟩** · trace: L06 01_AM_WITHDRAWAL_AND_REAPPLICATION.md line 194
· status: **PROPOSED — layer 6 (B-G10 term (d): the existing UNIQUE (application_id))**

| (d) | no `core_applicationwithdrawal` row exists for the application — **the existing `UNIQUE (application_id)`** (correction `04` §E2-Q2 (a)); a second withdrawal is refused | W5-b |

---

**⟨E2.058⟩** · trace: L07 01_FA_D1_WITHDRAWAL_FINALITY.md lines 204–209
· words replaced from L08 01_RD1_RD2_RD3_REPLACEMENTS.md line 213
· status: **PROPOSED — layer 7 (each undecided application may be withdrawn — waiting, pending or unable alike; W15) with layer 8 R2-a (RD-2 closed: W12 shows two sequential model writes, the second refused, the first remaining; no whole-transaction failure shown — LIMIT; G-1 OWED); DECIDED — CD-13 (W10 — 'the proposed treatment, §4' is HISTORY as a status: RT-35a/b); HQ-1 — an undecided applicant may request withdrawal while paused (MP-13)**
· supersedes: L06 01_AM_WITHDRAWAL_AND_REAPPLICATION.md lines 196–198; L07 01_FA_D1_WITHDRAWAL_FINALITY.md lines 205–207
· note ⟦editorial⟧: The layer-8 replacement line is itself blockquoted; the substituted text is the layer-8 line as stored (with its '> ' carrier), standing in place of the three layer-7 lines.

> **Each undecided application may be withdrawn** — waiting, pending or unable alike (W5-a; W15 after an UNABLE re-engagement). **A
> withdrawal recorded after a decision is refused whatever its effective time (W10 — the proposed treatment, §4); at equal record time the row already written wins (W11, W12); no existing command writes a withdrawal and a decision in one transaction (O-1 §(d)'s inventory names none). What W12 shows is two sequential model writes at one record time: the second is refused and the first remains admitted. No rollback is modelled, no whole-transaction failure is shown, and no production transaction policy is stated — whether a transaction containing both writes would abort is not shown and not decided (§10.4, G-1).** No stored status is added;
> WITHDRAWN stays derived from the row (§2A step 1–2). Withdrawal ends nothing else: the Learner's registration, access, memberships and
> other applications are untouched (U-3's distinctions).

---

**⟨E2.059⟩** · trace: L08 02_CLOCK_REPLACEMENTS_AND_OBLIGATIONS.md line 71
· status: **PROPOSED — layer 8 (C-d: B-G10's temporal scope — term (c) reads whether a decision row is on the record at W.recorded_at, a timestamp comparison; the model clock's assumption — LIMIT)**
· supersedes: L07 01_FA_D1_WITHDRAWAL_FINALITY.md lines 211–213

> B-G10's temporal scope is unchanged: it is evaluated on the insert of the withdrawal row and is not a standing check over later rows. Term (c) reads the record at that insert — whether a decision row is on the record at `W.recorded_at`, a timestamp comparison over the stored rows (§2), which the model clock's assumption makes coincide with the row's existence (§10.4) — rather than the decision's eligibility at the withdrawal's coordinates; terms (a), (b) and (d) are unchanged (`03_AM` §1.4's sentence is amended accordingly — `02_FA_D2` §5).

⟦editorial⟧ Layer 6 §7 (failures (a) and (b) reproduced on the old model and disposed on the new) and §8 (every resubmission reference disposed) — the amendment's disposition of the removed mechanism (DECIDED — the 23 Sep decision (1)):

---

**⟨E2.062⟩** · trace: L06 01_AM_WITHDRAWAL_AND_REAPPLICATION.md lines 264–268
· status: **PROPOSED — layer 6 (§9 what stands: FC-D4, FC-D6, FC-D5's first limb, the O-4 refusals, the withdrawal relation and its key)**

## 9. What stands, what changed, the limits

**Stands:** FC-D4 in full (the eligibility rule; the coordinate-bound CQ status; D4-a…f); FC-D6 in full (D6-a, D6-b); FC-D5's first limb
and its refusals (D5-i, D5-ii, D5-ii-b, D5-ii-c); every O-4 refusal (3, 4, 5, 6b, 7b, 8b); the UNABLE re-engagement (6c, W5-e); the
withdrawal relation and its key.

---

**⟨E2.063⟩** · trace: L06 01_AM_WITHDRAWAL_AND_REAPPLICATION.md lines 290–292
· status: **OPEN — AF-3 (1): a second undecided application without a link — no standing text constrains a Learner to one undecided application per LO (raised at the amendment, 23 Sep 2026; latest point: before the submission route is adopted for implementation); OP-W is keyed per application (layer 11)**

- **A second undecided application without a link (OBS).** No standing text constrains a Learner to one undecided application per LO;
  the model admits a second, unlinked one. Outside both decisions; neither decision turns on it; stated so a reader does not infer a
  constraint from the link.

⟦editorial⟧ Layer 7 §10 — the amendment's §9 replaced (consequential changes; abstractions; observed items), with layer 8's substitutions C-f (§10.1 (vii)), R2-b/C-b (§10.2), R1-m and R1-i (§10.3); and §10.4 is placed above with MP-12:

---

**⟨E2.064⟩** · trace: L07 01_FA_D1_WITHDRAWAL_FINALITY.md lines 230–237
· words replaced from L08 02_CLOCK_REPLACEMENTS_AND_OBLIGATIONS.md line 101
· status: **PROPOSED — layer 7 (§10.1 consequential design changes, (i)–(vii)) with layer 8 C-f ((vii): W-FIN's terms read what is on the record at the candidate's record time — a timestamp comparison over stored rows; LIMIT — the clock)**
· supersedes: L06 01_AM_WITHDRAWAL_AND_REAPPLICATION.md lines 270–273; L07 01_FA_D1_WITHDRAWAL_FINALITY.md lines 235–236
· note ⟦editorial⟧: 'no temporal architecture is selected' at the end of (vii) is HISTORY (CD-2).

### 10.1 "Consequential design changes, named" (lines 270–273), replacement

> **Consequential design changes, named:** (i) `core_applicationresubmission` removed; (ii) `predecessor_reference NULL` on the application
> record; (iii) B-G9′ on one limb with term (a); (iv) B-G10; (v) §2A step 0 (the application record read at coordinates) and the removal of
> the resubmission branch and `floor` from steps 2–3; (vi) supplement lines 98 and 110 re-worded; **(vii) W-FIN — the withdrawal term of
> B-G9 and of B-G9′ term (a), and B-G10 term (c), read what is on the record at the candidate's record time — a timestamp comparison over stored rows (§2; §10.4) — instead of eligibility at its coordinates; §2A steps 1–2 are one row and the read fails closed on a double terminal row.** No assignment edge is created; HD-E9's term
> is untouched; no stored status is added; no membership representation is selected; no temporal architecture is selected.

---

**⟨E2.065⟩** · trace: L07 01_FA_D1_WITHDRAWAL_FINALITY.md lines 239–247
· words replaced from L08 01_RD1_RD2_RD3_REPLACEMENTS.md line 227
· status: **LIMIT — layer 7 (§10.2 abstractions and untested assumptions) with layer 8 R2-b/C-b (two sequential writes, the second refused and the first remaining; no rollback modelled; the per-application record clock a model assumption — RD-2 closed; the clock closed on inspected text only)**
· supersedes: L06 01_AM_WITHDRAWAL_AND_REAPPLICATION.md lines 275–278; L07 01_FA_D1_WITHDRAWAL_FINALITY.md lines 244–246

### 10.2 "Abstractions and untested assumptions" (lines 275–278), replacement

> **Abstractions and untested assumptions:** integer times; no database; the submission checks reduced to eligibility tests on per-Learner
> rows and two LO facts; completion authority reduced to `route_enabled`; membership reduced to "an ACCEPT decision exists on this
> application"; the link's readability under access rules is not modelled — the ILC's access model governs it; B-G2 limb 3 not modelled;
> no concurrency; **no transactions — "one transaction" is modelled as two sequential writes at one record time, the second refused and the first remaining admitted; no rollback is modelled; the model keeps a per-application record clock, which is a model assumption and not a §5.3 guarantee: v0.13 §5.3 states that `recorded_at` is database-assigned and immutable and does not state that it is monotone, per application or otherwise (§10.4). In the examined sequences the clock's effects were W5-d's added reason on the retained cases and the refusal of the old W9 call order; `FA_W1`'s header and reason strings attribute the clock to §5.3 and are read under §10.4.** Reference-model results are not database-enforcement evidence.

---

**⟨E2.067⟩** · trace: L07 01_FA_D1_WITHDRAWAL_FINALITY.md lines 265–283
· words replaced from L08 01_RD1_RD2_RD3_REPLACEMENTS.md line 250
· status: **LIMIT — layer 7 (§11 demonstrations, negative and positive, on the reference model FA_W1 / FA_AF1; W14 the read failing closed — CD-14 decides the refusal's meaning) with layer 8 R2-d (W12: two sequential writes; the first remains admitted; no rollback modelled); the Reviewer's retained cases — LIMIT: inspected text and reference model only; no [E]**
· supersedes: L07 01_FA_D1_WITHDRAWAL_FINALITY.md line 276

## 11. Demonstrations — negative and positive, as the commission lists them

Every case is in `probes/FA_W1_withdrawal_finality_probe_RAW_OUTPUT.txt` by its case string; the Reviewer's exact sequences are in
`probes/FA_AF1_delivered_vs_corrected_RAW_OUTPUT.txt` with the delivered model beside the corrected one.

| the commission's item | case(s) | outcome on the corrected model | on the delivered model (recorded, FA_AF1) |
|---|---|---|---|
| the demonstrated sequence refused | **W9′**; FA_AF1 §A | withdrawal admitted; successor admitted; the late-recorded decision **refused** ("W-FIN: a withdrawal row is on the record at the decision's recorded_at") | admitted; (60, 60) DECIDED / WAITING |
| decision recorded before the withdrawal, so the withdrawal is refused | **W5-a** fourth case (retained); W11-a; W11-d | refused (B-G10 (c)) | refused |
| a withdrawal recorded after a decision whose effective time is later than the withdrawal's | **W10**; FA_AF1 §B | refused; the application never reads WITHDRAWN | admitted; DECIDED at (60, 60) and (1000, 1000) with the withdrawal eligible |
| ties at equal coordinates | **W11-a…d** | the terminal row already on the record wins, in either order and with either effective time | — |
| a withdrawal and a decision in one transaction | **W12** (two sequential writes at one record time); O-1 §(d) inventory | second write refused, the first remains admitted; no rollback modelled; no existing command writes both | — |
| an engagement recorded after the withdrawal with an earlier effective time | **W13-a** (ordinal 1), **W13-b** (ordinal 2, UNABLE basis); FA_AF1 §C | refused | W13-b's shape admitted (term (a) passed at (50, 60)) |
| every status read for the sequence, incl. (55, 60) and (60, 60) | **W9′-reads**, **W9′-grid** | §1's table; DECIDED nowhere | (55, 60) DECIDED; (60, 60) DECIDED |
| the earlier refusals preserved — all of `AM_W1`'s retained cases | the 54 retained cases, case strings unchanged, re-run by the carried `run()` | 54/54 as before (sweep check R1: every retained case's `got` identical to `AM_W1`'s raw output) | — |
| lawful fresh-application behaviour preserved | W2, W2-b, W3-a…h, W4, W6-a, W6-b, W7, W8 (retained); **W9′-successor** | the successor's own engagement and decision admitted; nothing inherited; the link authorises nothing; `member(appR)` False throughout | — |
| UNABLE re-engagement within a continuing application preserved | 6c, D4-c, W5-e (retained); **W15** | admitted | — |
| the membership consequence | **W9′-membership**, **W9′-successor** — through the model's simplified `member()` (an ACCEPT row exists on this application); it does not read the withdrawal or coordinates and does not show a landed membership chain | one ACCEPT for one Learner and one LO in this sequence | two ACCEPTs, one on the withdrawn application |
| the read reconciled | **W14** | a double terminal row (written directly, bypassing every guardian) makes `status()` fail closed | step 1 returned DECIDED |

---

**⟨E2.068⟩** · trace: L07 01_FA_D1_WITHDRAWAL_FINALITY.md lines 285–293
· status: **OPEN — AF-3 (1) several unlinked undecided applications by one Learner to one LO (W-FIN is per application); AF-3 (2) the owner of an application record and so its audience, including predecessor access through the link — both raised at the amendment (23 Sep 2026), visible, no policy invented; latest points: before the submission route / any read path through the link is adopted for implementation**

## 12. Where AF-3's two gaps touch this correction (visible; no policy invented)

- **Several unlinked undecided applications by one Learner to one LO.** W-FIN is per application: the first application's withdrawal or
  decision excludes nothing on a second, unlinked one (OBS retained: admitted). W9′-successor shows "one ACCEPT for one Learner and one LO"
  only because the successor is the sole other application in that sequence; with a second unlinked application, two ACCEPTs remain
  possible. That is gap 1, unchanged and visible; nothing here constrains it.
- **The owner of an application record, and so its audience, including predecessor access through the link.** W-FIN reads no link and
  changes no audience; `03_AM` §1.3's access row stands (`02_FA_D2` §3 restates it under CD-6's two readings without naming an owner). No
  claim about who may read a predecessor through the link is supported by anything here.

⟦editorial⟧ The mirror order (W10) and the boundary — layer 7 §4 with layer 8 R1-f (heading and opening words), R1-g (the third disposition row and the paragraph below it) and R1-h; the W10 status DECIDED by CD-13 (RT-35a/b):

---

**⟨E2.069⟩** · trace: L07 01_FA_D1_WITHDRAWAL_FINALITY.md line 144
· words replaced from L08 01_RD1_RD2_RD3_REPLACEMENTS.md line 98
· status: **PROPOSED — layer 7 as re-headed by layer 8 R1-f; W10 DECIDED — CD-13 (the heading's 'proposed treatment' is HISTORY as a status)**
· supersedes: L07 01_FA_D1_WITHDRAWAL_FINALITY.md line 144

## 4. The mirror order (W10) as this seat's proposed treatment, and the boundary of the rule

---

**⟨E2.070⟩** · trace: L07 01_FA_D1_WITHDRAWAL_FINALITY.md lines 146–147
· status: **PROPOSED — layer 7 (the demonstrated sequence needs no consequence-drawing; W-FIN's first half restates the issued sentences)**

**The demonstrated sequence needs no consequence-drawing.** The issued sentences name the case: an engagement created before the
withdrawal, a decision under it, the application read WITHDRAWN. W-FIN's first half restates them at the point of admission.

---

**⟨E2.071⟩** · trace: L07 01_FA_D1_WITHDRAWAL_FINALITY.md lines 149–156
· words replaced from L08 01_RD1_RD2_RD3_REPLACEMENTS.md line 104
· words replaced from L08 01_RD1_RD2_RD3_REPLACEMENTS.md line 114
· status: **PROPOSED — layer 7 with layer 8 R1-f/R1-g (the three dispositions; the third the proposed treatment); DECIDED — CD-13 option A adopts the third disposition's behaviour (RT-35a/b): 'this seat proposes a treatment that the issued words do not settle' is HISTORY as a status**
· supersedes: L07 01_FA_D1_WITHDRAWAL_FINALITY.md line 149; L07 01_FA_D1_WITHDRAWAL_FINALITY.md line 156

**The mirror recording order (W10) is where this seat proposes a treatment that the issued words do not settle.** A decision effective 60 recorded 58, then a
withdrawal effective 55 recorded 60. Three dispositions were possible on the delivered text:

| disposition | what it produces | against the words |
|---|---|---|
| admit the withdrawal under the delivered B-G10 (c) (eligibility: the decision is not effective at 55) and keep the read order | the application is withdrawn effective 55 **and** decided effective 60; at (60, 60) and (1000, 1000) it reads DECIDED **while the withdrawal is eligible** (FA_AF1 §B "delivered") | fails "remains withdrawn" in effective order; the Reviewer's stated test ("any remedy that still reads DECIDED at a coordinate where the withdrawal is eligible fails the decision as worded") is failed |
| admit the withdrawal and let the read put the withdrawal first, masking the decision | the decision row exists, is eligible, and is not operative in reads where the withdrawal is eligible | that is the operativeness question — whether a record stays operative once knowledge shows its premises incomplete — which is `LD-D1`'s, undecided; taking it here would select a temporal policy through the repair |
| **refuse the withdrawal: a decision on the record excludes it (§7.1) — the proposed treatment** | one terminal row per application; every read coherent; no operativeness question arises | leaves neither issued sentence false, but only on this seat's reading of them: "remains withdrawn" is satisfied vacuously (nothing has been withdrawn), and *undecided* is read as "no decision row on the record at the withdrawal's record time, whatever either effective time" — a definition the issued sentence does not give (Reviewer follow-up `01_DISPOSITIONS.md` §2) |

---

**⟨E2.072⟩** · trace: L08 01_RD1_RD2_RD3_REPLACEMENTS.md line 128
· status: **PROPOSED — layer 8 (R1-g: the paragraph below the table — the exact point at which a different disposition would need a Human decision); DECIDED — CD-13 (24 Sep 2026) supplies that decision: the first accepted terminal outcome stands; the back-dating question is closed by CD-13's words 'regardless of its supplied effective date'**
· supersedes: L07 01_FA_D1_WITHDRAWAL_FINALITY.md lines 158–162

> The third is this seat's proposed treatment. The reason for proposing it is a design reason: of the three it is the one that draws on no temporal policy and leaves neither issued sentence false. It is not an additional Human ruling, and it is not a consequence the issued words establish — the Reviewer's follow-up (`01_DISPOSITIONS.md` §2) records the definition of *undecided* as this seat's and adopts neither this disposition nor its opposite; nothing here adopts either. **The exact point at which a different disposition would need a Human decision:** whether a withdrawal that back-dates its effective time before a recorded decision's effective time may be admitted against that decision. The issued words neither admit nor refuse it; admitting it would also reopen the masking question inside `LD-D1`. Not surfaced as a decision by this correction, which is commissioned not to ask for one (`00_RETURN_NOTE.md` §4.3 states the point so it is not mistaken for silence; the Vision Chamber carries it to the Human Governor separately).

---

**⟨E2.073⟩** · trace: L07 01_FA_D1_WITHDRAWAL_FINALITY.md lines 164–175
· status: **PROPOSED — layer 7 (the boundary — what W-FIN does not touch: B-G9′ term (a)'s decision half keeps the eligibility reading; not LD-D1, and why)**

**The boundary — what W-FIN does not touch.** B-G9′ term (a)'s decision half ("a decided application admits no engagement") keeps the
delivered eligibility reading. An engagement recorded after a decision with an effective time before the decision's is a case of the same
shape for a different pair of record kinds; the issued decision says nothing about it and the commission forbids generalising, so it is
left as delivered and named here. Nothing in the combined design writes such a row (B-G9′'s one limb needs the current engagement to read
UNABLE at the row's coordinates, and B-G9 refuses a decision on an engagement whose assignment has ended, (iii)); it is stated as the
boundary, not as a case, and is not surfaced as a decision. Every other term of B-G9, B-G9′ and B-G10 keeps FC-D4's own-coordinates rule.

**Not `LD-D1`, and why.** `LD-D1` asks whether a record remains operative in a view whose knowledge shows its premises were incomplete,
and which of architectures A, B or H should govern that, with the v0.13 amendment map still unread (RV-11). W-FIN never lets the case arise
for an application's terminal acts: it refuses the second row at admission, so no view holds two terminal records whose operativeness
would have to be weighed. It keeps `LD-D1`'s full scope, decides no part of it, and is not a member of it. The amendment's parenthetical
"strict record order vs effective order" is withdrawn with the W9 bullet (§10.3).

---

**⟨E2.074⟩** · trace: L08 01_RD1_RD2_RD3_REPLACEMENTS.md line 141
· status: **PROPOSED — layer 8 (R1-h: the repair stated without a temporal policy); W10 DECIDED (CD-13)**
· supersedes: L07 01_FA_D1_WITHDRAWAL_FINALITY.md lines 177–178

> **If the repair could not have been stated without a temporal policy, this section would say so and stop.** It could: W-FIN's first half is the issued words; its second half is this seat's proposed treatment of the mirror order, which draws on no temporal policy and which the issued words neither establish nor exclude; the boundary is named; the read fails closed rather than choosing.

⟦editorial⟧ §10.4 — what the model assumes and what the design owes (added by layer 8 A-1), with its W10 and fail-closed status words replaced by layer 9 RT-35c (CD-13 / G-1) and RT-35d (CD-14):

---

**⟨E2.075⟩** · trace: L08 02_CLOCK_REPLACEMENTS_AND_OBLIGATIONS.md lines 149–161
· words replaced from L09 02_REPLACEMENTS_AND_ADDITIONS.md line 264
· words replaced from L09 02_REPLACEMENTS_AND_ADDITIONS.md line 269
· status: **LIMIT — the clock is a model assumption (not a §5.3 guarantee; closed on inspected text only); OWED — G-1 (the exclusion of a conflicting terminal row under concurrency; mechanism PROPOSED at MP-12; §6.5's reach undecided — for A1), G-2 (recorded_at order vs admission order); DECIDED — CD-13 (which row is excluded), CD-14 (the fail-closed read as an integrity refusal)**
· supersedes: L08 02_CLOCK_REPLACEMENTS_AND_OBLIGATIONS.md line 157; L08 02_CLOCK_REPLACEMENTS_AND_OBLIGATIONS.md line 161
· note ⟦editorial⟧: Carried as a blockquote in its source; rendered with the carrier retained.

> ### 10.4 What the model assumes and what the design owes — the clock, G-1, G-2, §6.5 (added by the RD/clock correction; adopts nothing)
>
> **The clock is a model assumption.** The corrected model keeps a per-application record clock: an admission whose `recorded_at` precedes a row already on that application's record is refused. v0.13 §5.3 (lines 1101–1114) states that `recorded_at` is database-assigned and immutable and that `occurred_at <= received_at <= recorded_at`; it does not state that `recorded_at` is monotone, per application or otherwise, that equal values are forbidden, that a caller-supplied `recorded_at` is rejected, or that one application's rows are ordered by a per-application clock. Every attribution of monotonicity to §5.3 in `FA_W1`'s header (lines 42–47), its comments (lines 67, 211) and its reason strings (`MODEL (v0.13 §5.3, recorded_at is database-assigned) …`; `… not a physical sequence under §5.3 …`) is read as the model's assumption under this section; those bytes and the raw outputs that carry them are preserved unedited and their body digests stand (`03_SUPERSESSION_MAP.md` SM-RD-P1 … P4).
>
> **Three things, distinguished and not equated:** *stored-row existence* — a terminal row has already been admitted to the store; *timestamp comparison* — `W.recorded_at ≤ NEW.recorded_at` over the stored rows, which is the test W-FIN's three terms perform (§2); *admission order* — the order in which admissions take effect. Under the model clock the three coincide. That coincidence is the model's assumption; it is not established by recording time being database-assigned.
>
> **What the clock did in the examined sequences, as the Reviewer demonstrated it** (follow-up `01_DISPOSITIONS.md` §4; `RV_FA_INDEPENDENT_WFIN_CLOCK_AND_W14.py`). With the clock live, the decision recorded at 60 after the withdrawal recorded at 58 (W9′, the physical order) is refused by W-FIN alone; with the clock disabled and W-FIN's code unchanged it is still refused by W-FIN alone, and (55,60) and (60,60) still read WITHDRAWN. The old W9 call order — a decision recorded at 60 written before a withdrawal recorded at 58 — is excluded by the clock assumption: clock live, the withdrawal is refused by the clock, not by W-FIN's mirror, and (60,60) reads DECIDED because the withdrawal was not stored; clock disabled, both rows are stored and the read then fails closed (W14's class), not DECIDED. On the 54 retained cases the clock's effect was W5-d's added reason, outcome unchanged. Nothing is stated about sequences not examined, and §5.3 is not said to make the old W9 order impossible. The model still accepts a caller-chosen `recorded_at` whenever it is not earlier than that application's clock, models no `received_at`, and checks no retrospective or prospective basis (W10's comment "§5.3 permits, with a basis" names a check the model does not perform).
>
> **G-1 — an unresolved design obligation; enforcement evidence owed later.** No application may hold two terminal rows — a `core_applicationdecision` row and a `core_applicationwithdrawal` row — both admitted as standing: the design must exclude the conflicting terminal row, **including under concurrent admission by two transactions**, which the model neither runs nor enforces (its per-table uniqueness keys — one decision per application, one withdrawal per application — do not, on their own words, prevent one row of each kind from committing; no cross-kind exclusion is stated anywhere; no [E] exists). **The exclusion is the obligation.** **Which row is excluded is a separate matter:** W-FIN's proposed treatment is "the first admitted excludes the other" in both recording orders, whose mirror half (a decision on the record excluding a later-recorded withdrawal) is W10 — **decided (CD-13)**. Which row is excluded is therefore settled; the exclusion under concurrency remains G-1's obligation, stated at MP-12. G-1's wording does not settle W10; a different treatment of W10 would still owe G-1's exclusion. This correction selects no mechanism — no lock, sequence, cross-kind guardian, trigger, isolation level or rollback rule. **Any statement of G-1 must be reconciled with v0.13 §6.5 (*Serialization*, lines 1583–1593)**, which requires every write capable of changing membership eligibility, contextual-role occupancy, Circle participation, essential coverage or restricted-continuity state to acquire the affected immutable `LivingOrganism` root as the shared serialization resource before evaluation or persistence, states that a deferrable commit trigger re-evaluates the invariant after serialization and that a deadlock or serialization failure remains a failure, and requires PKT-B's guardians to call PKT-A-2's lock helper. **Whether B-G9's or B-G10's writes fall within §6.5's list — and so §6.5's reach over G-1 — is not decided here.**
>
> **G-2 — an unresolved design obligation; enforcement evidence owed later.** Either one application's `recorded_at` order agrees with its admission order, or W-FIN and the §2A read do not rely on that agreement. Neither is chosen here, and W-FIN's timestamp test is not restated as a different predicate.
>
> **The fail-closed read (§1) is decided as an integrity refusal (CD-14, 24 Sep 2026, option A; RT-36).** The Reviewer's independent cells show that once both terminal rows are stored, every read at which the application record is eligible fails closed — including coordinates at which neither terminal row is eligible under §5.2, and coordinates at which only one is; the Reviewer names a possible tension with §5.2 and §5.3 (if those clauses forbid a not-yet-recorded row from affecting a view) and does not settle it. It is not settled here either: the read's behaviour (§1; W14) stands as delivered, and its disposition or dependency is the Vision Chamber's to carry to the Human Governor.

⟦editorial⟧ MP-12 — G-1's statement with §6.5's reach (layer 9, PROPOSED):

---

**⟨E2.076⟩** · trace: L09 03_MECHANISM_PROPOSALS.md lines 137–145
· status: **PROPOSED — layer 9 (MP-12: the invariant G-1; which row — DECIDED CD-13; the mechanism (a) both guardians acquire the LO root under §6.5, (b) a deferrable commit re-check; §6.5's reach — this proposal's reading, for A1 to challenge; G-2 the claim to test); OWED — enforcement evidence; LIMIT — concurrency unmodelled (EL-4)**

## MP-12 — G-1's statement, with §6.5's reach, now that CD-13 is decided

**Proposal.** *The invariant (G-1).* No application may hold two standing terminal rows. *Which row (decided, CD-13).* The first accepted by the system stands; the conflicting later write is refused. *The mechanism (proposed; enforcement evidence owed later).* (a) Both B-G9 (the decision guardian) and B-G10 (the withdrawal guardian) **acquire the application's LO root under v0.13 §6.5 before evaluation** — the proposal is that they fall within §6.5's list because a decision changes membership eligibility (its effect is the membership `PROPOSED`) and a withdrawal forecloses it; this brings the two writes into one serialisation domain so that two transactions cannot each see an empty terminal set. (b) A deferrable commit re-check (§6.5's "re-evaluates the invariant after serialization"): at commit, exactly one terminal row exists for the application. (c) No cross-kind unique index is proposed (two relations); the lock plus the deferred re-check is the exclusion. *§6.5's reach.* Stated as this proposal's reading, not decided: the decision and withdrawal guardians are within §6.5 because of their membership consequence; A1 should challenge it. *G-2* stays open: whether `recorded_at` order agrees with admission order, or whether W-FIN's test can avoid relying on it — under (a)+(b) W-FIN's timestamp comparison is performed inside the lock, so admission order and record order coincide by construction for these two kinds; that is the claim to test, not a settled fact.

**Alternatives.** (i) A single terminal relation with a `kind` column and `UNIQUE (application_id)` — a schema change to two landed proposals; cleaner, but it merges two records with different actors and audiences. (ii) Serialisable isolation without the root lock — leaves the ordering to the database's retry behaviour.

**Recommendation `[A]`.** (a)+(b).

**Evidence.** [T] §6.5 lines 1583–1593; L8 §10.4; CD-13. [M] W12 as corrected (sequential, second refused, first remains). Concurrency **unmodelled**; no [E].

⟦editorial⟧ MP-13 — the withdrawal-request evaluation (layer 11, AT11-01), with its spans as replaced by layer 12 RT12-02 … RT12-07:

---

**⟨E2.077⟩** · trace: L11 01_REPLACEMENTS_AND_ADDITIONS_L11.md lines 59–81
· words replaced from L12 02_REPLACEMENTS_AND_ADDITIONS_L12.md line 34
· words replaced from L12 02_REPLACEMENTS_AND_ADDITIONS_L12.md line 39
· words replaced from L12 02_REPLACEMENTS_AND_ADDITIONS_L12.md line 44
· words replaced from L12 02_REPLACEMENTS_AND_ADDITIONS_L12.md line 49
· words replaced from L12 02_REPLACEMENTS_AND_ADDITIONS_L12.md line 54
· words replaced from L12 02_REPLACEMENTS_AND_ADDITIONS_L12.md line 59
· status: **DECIDED — the AV-2 direction of 24 Sep 2026 (the four steps and their order; a CQ result is evidence, not a terminal record; an admitted withdrawal never displaced; an open or incomplete CQ does not block withdrawal; the recorded route governs; no CQ newly concluded during deactivation; the coordination requirement and the fixed cutoff; CARE chooses nothing) and its clarification; AV-2 CLOSED WITH LIMITS (24 Sep 2026) — no remaining defect or Human question; PROPOSED — layers 11–12 (the operation's name and signature; B-G9 (vii); OP-W's realisation; the cutoff's anchor received_at (DQ-18); the evaluation coordinates (T_cut, T_cut) (DQ-24); cq_conclude_if_due, cq_close; the CARE kind APPLICATION_WITHDRAWAL_NOT_ADMITTED; DQ-19, DQ-23); OWED — DISCUSSION's determinacy and prompt recording (EL-14), the unbuilt receiving surface, G-1, G-2; LIMIT — RC_P3/RC_P4 sequential, not concurrency evidence (EL-11, EL-13)**
· supersedes: L11 01_REPLACEMENTS_AND_ADDITIONS_L11.md line 63; L11 01_REPLACEMENTS_AND_ADDITIONS_L11.md line 67; L11 01_REPLACEMENTS_AND_ADDITIONS_L11.md line 69; L11 01_REPLACEMENTS_AND_ADDITIONS_L11.md line 73; L11 01_REPLACEMENTS_AND_ADDITIONS_L11.md line 75
· note ⟦editorial⟧: Rendered with the source's '> ' blockquote carrier retained on every line.

> ## MP-13 — The withdrawal-request evaluation: the application and its deciding CQ, evaluated together (the direction of 24 September 2026; AV-2)
>
> **What it is.** *One operation*, `withdraw_application(application, applicant_party, effective_at, received_at)`, replacing the shape "check the status, then write the withdrawal": it evaluates the application's terminal record and its deciding CQ together, in the four steps below, under one ordering point, and writes at most one terminal row. Where the Human Governor's direction fixes behaviour, the words below carry it; the records, guardian terms, interfaces and CARE kinds are proposals.
>
> **§1 The four steps, in order.** Let `T_cut` = the request's `received_at` (**the evaluation cutoff**, §4) and `T_eval` = the evaluation time under OP-W (§3), which is the `recorded_at` of whichever terminal row this operation writes. *Step 1 — an already-admitted terminal outcome stands.* If any terminal row is on the application's record now (a present-store test, past eligibility, like W-FIN's own "the row exists"), the operation stops: the first admitted terminal outcome stands (CD-13 / W-FIN), whatever any effective time and whatever `T_cut`; the request is refused and CARE explains (§6). *Step 2 — the deciding CQ's completion position.* On a **recorded** `CQ` route (R1/R3 — the engagement row's `route` and `settings_basis`, B-G9 (i), (b); the LO's current `cq_active` setting alone never replaces it, §5), read at the **evaluation coordinates `(state_at = T_cut, known_at = T_cut)` — both fixed at receipt**: (a) *evidence established at receipt* — a result DISCUSSION had recorded by `T_cut` (`cq_status(engagement_reference, T_cut, T_cut)`, FC-D4 §4 — eligible iff `result.effective_at ≤ T_cut` and `result.recorded_at ≤ T_cut`) is a candidate if it is a *qualifying* result (`YES` or `NO`; an `INCOMPLETE` conclusion is not); (b) otherwise, *the already-due evaluation* — if the CQ was **already due at `T_cut`** (its hold-adjusted deadline, counting unpaused time only, DQ-16, had been reached at or before `T_cut`), the evaluation of the CQ **as of that deadline** was authorised by receipt, and its outcome is the candidate **whenever it is performed or recorded**: `cq_conclude_if_due(engagement_reference, as_of = T_cut, at = T_eval)` (§7) returns that outcome — a qualifying result already on DISCUSSION's record with `effective_at` at or before the deadline (`ALREADY_CONCLUDED`), or, where none is and the evaluation is **lawful now** (the CQ is not held by an R1-D13 pause at `T_eval` and not closed), the evaluation DISCUSSION performs now (`CONCLUDED_DUE`, `effective_at` the instant its rule was satisfied, at or before the deadline; `recorded_at` = `T_eval`); the evaluation is determinate (§7), so the outcome is the same whichever of the two it is. **Nothing else counts**: a result whose `effective_at` is after `T_cut` is a conclusion reached after receipt; a result recorded after `T_cut` on a CQ not due at `T_cut` is later-recorded evidence — neither establishes the decision in this evaluation, however long before `T_eval` it was recorded (§4; RC_P4 K1, K4, K6). A CQ that is open and not due, or due but held, or concluded `INCOMPLETE`, or whose only result is of those two kinds, yields no candidate — **an open or incomplete CQ does not itself block withdrawal**, and nothing is newly concluded during LO deactivation. On a recorded `CSA` route there is no deciding CQ: steps 2–3 are vacuous (no automatic evaluation exists; the 27-day trigger, R1-D17, produces no decision). *Step 3 — the decision first.* If a candidate exists, the operation admits the decision **through its authorised route**: a `core_applicationdecision` row (route `CQ`, deciding party the LO or the Circle, `outcome` `ACCEPT` for `YES` / `DECLINE` for `NO`, `supporting_result_reference` the result; actor the deciding Organism or Circle, enterer CARE — the same writer R1/R3 name) under **B-G9, every term including (vi) and (vii)**, with the timestamps of §4. If B-G9 admits it, the withdrawal request is refused as at step 1 and CARE explains (§6). If B-G9 refuses it (authority or capacity fails at the decision's coordinates; the result is not eligible there; the deciding Circle is `CLOSED`; the LO folds `DORMANT` at the decision's coordinates), the result establishes no decision and the operation proceeds to step 4 with that refusal in the CQ's disposition. *Step 4 — the withdrawal.* The operation admits the `core_applicationwithdrawal` row under B-G10 (terms (a)–(d) as delivered), with the timestamps of §4; it closes the CQ to further participation (`cq_close(engagement_reference, at = T_eval, reason = APPLICATION_WITHDRAWN)`, §7 — participation and any result already concluded are retained as evidence; no result is produced after close); and CARE explains the withdrawal and the CQ's disposition (§6). **Both halves of the prevention**: (i) any later `core_applicationdecision` write — by the ordinary route or by the background writer that turns DISCUSSION results into decision rows — is refused by B-G9 (vii), which every writer passes; (ii) the closed CQ yields no new result. A result concluded before the close, or by a DISCUSSION that could not close, stays a result record and creates no decision.
>
> **§2 What the direction fixes, and what is proposed.** Fixed: the four steps and their order; a CQ result is evidence, not a terminal record; an admitted withdrawal is never displaced by a later decision carrying an earlier effective date; an open or incomplete CQ does not block withdrawal; the recorded route governs; no CQ required to remain paused is newly concluded during deactivation, and a valid pre-pause result is recognised subject to terminal-record finality; CARE's explanations (§6) and that CARE chooses nothing; the coordination requirement and the fixed cutoff. Proposed: the operation's name and signature; B-G9 (vii)'s wording; OP-W's realisation (§3); the anchor of the cutoff (§4, DQ-18); the evaluation coordinates; the DISCUSSION interfaces `cq_conclude_if_due` and `cq_close` (§7); the CARE kind `APPLICATION_WITHDRAWAL_NOT_ADMITTED` and the notice contents (§6); the consequences stated at DQ-19 and DQ-23.
>
> **§3 The ordering point OP-W.** *Requirement (fixed by the direction).* The evaluation and the terminal write are one serialised unit against concurrent completion and withdrawal: a separate status check followed by an unprotected write is not this operation. *The point (proposed).* **OP-W is a transaction-scoped exclusive serialisation resource keyed on the application's identity**, acquired before step 1 and held until the transaction commits or fails, by **every writer of a terminal row for that application** — this operation, the ordinary decision route, and the background writer of CQ results — so that for one application the terminal writes are totally ordered and each writer evaluates B-G9 or B-G10 against the record as the previous writer left it. Proposed realisation: PostgreSQL `pg_advisory_xact_lock` keyed on the application's UUID (through PKT-A-2's lock helper where its form admits a non-root key, else a helper of the same shape) — alternative: `SELECT … FOR UPDATE` on the application record's row (DQ-21). *Relation to v0.13 §6.5 — a dependency, not decided here.* MP-12 (a) proposes that B-G9 and B-G10 fall within §6.5's list because a decision changes membership eligibility and a withdrawal forecloses it. **Where that reading holds, the LO root lock is acquired first, in §6.5's domain, and OP-W inside it — lock order root → application, never the reverse; multi-organism operations acquire roots in canonical UUID order or are refused; a deadlock or serialisation failure remains a failure (§6.5).** Where that reading does not hold, OP-W alone is the ordering point for terminal writes; nothing in MP-13 depends on §6.5's reach except the outer lock's presence — A1 should test both. *Relation to G-1.* G-1 (no application holds two standing terminal rows) is the invariant; OP-W is the proposed means by which the invariant is meant to hold at admission; §6.5's deferrable commit re-check (MP-12 (b)) remains the second line, re-evaluated after serialisation. *Relation to G-2.* Under OP-W, for one application's terminal rows, admission order and `recorded_at` order coincide by construction — the claim MP-12 already names as the one to test; W-FIN's "at equal record time, the one already written" cannot arise for two terminal rows of one application under OP-W. *Where this operation runs — receipt is the coordinated check.* The receiving surface receives the applicant's act **by running this operation**: it sets `received_at` (`T_cut`) as the operation's first act, acquires OP-W, and performs steps 1–4 in the same request and transaction. **There is no queued form**: an act the platform cannot evaluate is not received — nothing is recorded and the applicant is told so. An LO pause defers nothing (the direction; HQ-1): the request is evaluated during the pause, and step 2 (b)'s hold rule governs the CQ. The interval between `T_cut` and `T_eval` is therefore the operation's own latency; under §4 it changes nothing in step 2, and step 1 sees only what a writer serialised ahead of this operation at OP-W has admitted (CD-13; DQ-23). Should any waiting ever be interposed between receipt and evaluation, the behaviour is preserved by the same two facts: step 2's evidence base is fixed at `T_cut` in both dimensions, and the already-due evaluation is determinate as of a deadline ≤ `T_cut` — only step 1's CD-13 window would widen.
>
> **§4 Timestamps, under v0.13 §5.3.** *The evaluation cutoff* `T_cut` = the withdrawal request's **`received_at`** — the time the platform's receiving surface received the applicant's act, set by that surface at receipt as the first act of the operation itself (§3: receipt is the coordinated check; a requirement on the request path; never set by the applicant). It is the recorded time of the request that is neither the applicant's own claim nor a time that moves with processing: `occurred_at` is applicant-supplied and could be back-dated to pre-empt a result; `recorded_at` is database-assigned at the write and is exactly what processing delay advances (DQ-18). **The cutoff never advances because processing was delayed**: a result whose `effective_at` is later than `T_cut`, or a deadline reached after `T_cut`, does not establish the decision in this evaluation, however long before `T_eval` — or before the LO's deactivation — it occurred (RC_P3 W7a, W7c). *The evaluation coordinates* for step 2 are `(state_at = T_cut, known_at = T_cut)` — **both fixed at receipt**. §5.3's knowledge dimension is exactly the one processing delay advances: a view taken at `T_eval` shows every record made before `T_eval`, so reading step 2 at `known_at = T_eval` let a later evaluation see a later record (RC_P3 W9 — the superseded coordinates, now history for that claim). Fixing `known_at` at `T_cut` makes the evidence base what the platform's record held when the act arrived: *evidence established at receipt* is a result recorded by `T_cut` (its `effective_at` ≤ `T_cut` follows); *a conclusion reached afterwards* is a result with `effective_at` after `T_cut`, or one recorded after `T_cut` on a CQ not yet due — neither counts, however long before `T_eval` it was recorded. **The one later-recorded thing that counts is the already-due evaluation** (step 2 (b)): for a CQ whose deadline had been reached by `T_cut`, the evaluation as of that deadline was authorised by receipt and is determinate (§7) — the same outcome and effective time whether DISCUSSION had already recorded it, records it at `T_eval`, or would have recorded it at any other time — so the outcome does not depend on processing time (RC_P4 K1–K6; the invariance sweep K6). `T_eval` plays three roles only: step 1's present-store test (an admission that already occurred stands — CD-13; DQ-23), whether the already-due evaluation may lawfully be performed now (the hold; DQ-19), and the `recorded_at` of whichever row is written. *A decision admitted in step 3:* `decided_effective_at` = the result's `effective_at` (the conclusion date, or the deadline reached under step 2 (b)); `received_at` = `T_eval` (a system-entered act: receipt is the evaluation); `recorded_at` database-assigned; where `decided_effective_at` < `received_at` the row carries `temporal_basis_kind = RETROSPECTIVE` with `supporting_result_reference` as its basis reference (PKT-A-2 enforces kind, non-emptiness and polarity; the referent check is PKT-B's, §11.1). *An admitted withdrawal:* `effective_at` = the applicant's requested effective time (B-G10 as delivered; a retrospective value carries the applicant's request as basis); `received_at` = `T_cut`; `recorded_at` database-assigned. The withdrawal's effective time plays no part in finality; nor does a decision's — admission order does (CD-13). *Step 1* reads no coordinates: it asks whether a terminal row exists now.
>
> **§5 The recorded route governs.** Step 2 consults the deciding CQ iff the engagement row's recorded route is `CQ` (R1/R3), whatever the LO's `cq_active` setting is at `T_eval`: a setting since changed to `No` neither closes the CQ nor lets a Coordinator self-assign the application's CSA in its place (B-G9′'s one limb opens a new ordinal only from `UNABLE`, which an open or concluded-with-result CQ is not); a setting since changed to `Yes` on a recorded `CSA` route makes no CQ the deciding route (RC_P3 W8a, W8b). Completion authority is read as at the engagement's `settings_basis` (correction `04` §E2-Q2 (b)); B-G9 reads the engagement row, never the live settings fold.
>
> **§6 CARE — explanations, not choices.** CARE states facts and the parties' acts; it chooses no outcome and never withdraws for the applicant. *Acceptance stands* (step 1 with a `DECISION ACCEPT` row, or step 3 admitting one): `APPLICATION_WITHDRAWAL_NOT_ADMITTED` to the applicant's own notice channel — the application was accepted effective `decided_effective_at`; the withdrawal request is not admitted because the first admitted terminal outcome stands; membership departure is available through the membership-departure route (U-3's departure act), which CARE explains and does not initiate. *Decline stands* (`DECISION DECLINE`): the same kind — the application concluded with a decline effective `decided_effective_at`; there is nothing to withdraw; re-application is a new application under the LO's rules (§2B). *Already withdrawn* (a `WITHDRAWAL` row): the same kind — the withdrawal already stands (B-G10 (d)). *Withdrawal admitted* (step 4): `APPLICATION_WITHDRAWN` to the applicant's own channel and to the LO board (the deciding Circle's board for Circle routes) — the withdrawal effective `effective_at`, the cutoff, and **the CQ's disposition** as fact: no result (the CQ was closed to further participation at `T_eval`; participation retained); a result that did not establish the decision (it was not established at receipt — its conclusion is after the cutoff, or its record was made after the cutoff on a CQ that was not yet due at the cutoff — or the authorised route refused it, with B-G9's reason; the notice states which, in those terms: §4's rule and this sentence are one rule); a due evaluation not performed because the CQ was held by the pause; on a `CSA` route, no deciding CQ. In every case: any CQ result, whenever concluded, creates no decision on this application. *The pause notice* (`APPLICATION_PAUSED_APPLICANT`, RT10-10 (b) as corrected by RT11-02) offers to *request* withdrawal and states the deciding position at its own coordinates. *Notice records* follow PC-24 as delivered (the atomic post/cancel contract; `notice_key` uniqueness); `APPLICATION_WITHDRAWAL_NOT_ADMITTED` is a new kind proposed in PC-24's series. Under the standing CARE constraint, no notice judges the applicant's or the LO's decision.
>
> **§7 DISCUSSION interfaces (owner DISCUSSION; proposed; unimplemented — EL-6).** `cq_status(engagement_reference, state_at, known_at)` as at FC-D4 §4. **`cq_conclude_if_due(engagement_reference, as_of, at)`** → `ALREADY_CONCLUDED(result)` | `CLOSED` | `NOT_DUE(adjusted_deadline)` | `HELD(adjusted_deadline)` | `CONCLUDED_DUE(result)`: for a CQ whose hold-adjusted deadline ≤ `as_of`, DISCUSSION answers **what the CQ's conclusion is as of that deadline** — a pure function of the participation it had recorded as at the deadline. Checks in this order: `CLOSED`; `NOT_DUE` (the deadline is after `as_of`); `ALREADY_CONCLUDED(result)` where a qualifying or `INCOMPLETE` result with `effective_at` at or before the deadline is on DISCUSSION's record at `at` (reading it concludes nothing, so the hold does not apply); `HELD` where performing the evaluation would newly conclude the CQ while it is held at `at`; else `CONCLUDED_DUE(result)` — the evaluation performed now, `effective_at` the instant DISCUSSION's rule was first satisfied (at or before the deadline; the deadline itself where only the deadline rule concludes it), `recorded_at` = `at`. **The evaluation is determinate (EL-14): the same outcome and the same `effective_at` whenever it is performed or recorded**, which is what makes step 2 (b) independent of processing time. A result DISCUSSION recorded by its own later act with `effective_at` after the deadline is not the conclusion as of the deadline and is never returned by this interface (it stays a result record — evidence). Whether the rule yields `YES`, `NO` or `INCOMPLETE` is DISCUSSION's (EL-10). **`cq_close(engagement_reference, at, reason)`**: the question is closed to further participation from `at`; participation already recorded is retained; a result concluded before `at` is retained as a result record; no result is produced after close. `cq_hold` / `cq_release` as at RT10-10 (b). A DISCUSSION that cannot close does not defeat step 4's prevention: B-G9 (vii) is the binding half (DQ-20).
>
> **Alternatives.** (i) Treat a received-but-unevaluated withdrawal request as admitted at receipt — rejected: admission is the guarded write under OP-W, and CD-13's "first accepted by the system" is an admission, not a receipt (DQ-23). (ii) Anchor the cutoff on `recorded_at` — rejected: it advances with delay (DQ-18). (iii) Let the withdrawal be admitted whenever the decision row is absent, leaving the pre-pause result unused — rejected by the direction's steps 2–3. (iv) Let the CQ run to conclusion after the withdrawal, its result unused — not adopted; `cq_close` is proposed so members are not asked to keep deciding a withdrawn application; the guardian prevents the decision either way (DQ-20).
>
> **Recommendation `[A]`.** As proposed.
>
> **Evidence.** [T] the direction of 24 September 2026; CD-13; W-FIN (L7 01 §2); B-G10 (L6 01 §4; L7 01 §7); B-G9 (i)–(vi) (L5 03 §3, §4; L9 03 MP-1); MP-12; v0.13 §5.2, §5.3, §6.5. [M] RC_P3 family W (W1a–W13, sequential) and family L (scripted interleavings — not concurrency evidence). Concurrency (G-1, G-2) **unmodelled and owed**; no [E].

⟦editorial⟧ The design questions resolved within function for MP-13 (layer 11 AT11-03; layer 12 AT12-01; layer 13 AT13-01), with DQ-19 and DQ-23's grounds as replaced by layer 12 RT12-08/RT12-09 — placed here because they qualify MP-13 and the status read:

---

**⟨E2.078⟩** · trace: L11 01_REPLACEMENTS_AND_ADDITIONS_L11.md lines 88–93
· words replaced from L12 02_REPLACEMENTS_AND_ADDITIONS_L12.md line 64
· words replaced from L12 02_REPLACEMENTS_AND_ADDITIONS_L12.md line 69
· status: **resolved within function — DQ-18 (received_at), DQ-19, DQ-20 (cq_close proposed; B-G9 (vii) binding), DQ-21 (OP-W: pg_advisory_xact_lock nested under §6.5's root lock where MP-12's reading holds — for A1), DQ-22 (CSA route), DQ-23 (a terminal row admitted ahead at OP-W stands — CD-13); none a Human question; flagged so they are not mistaken for silence**
· supersedes: L11 01_REPLACEMENTS_AND_ADDITIONS_L11.md line 89; L11 01_REPLACEMENTS_AND_ADDITIONS_L11.md line 93

> | **DQ-18** | AV-2 — which recorded time of a withdrawal request fixes the evaluation cutoff | **`received_at`**, set by the receiving surface at receipt (resolved within function) | `occurred_at` is the applicant's own input and could pre-empt a result by back-dating; `recorded_at` is assigned at the write and is exactly what processing delay advances; `received_at` is the platform's own record of when the act arrived, fixed before any queue — the only one of §5.3's four times that is both platform-recorded and delay-invariant; the direction forbids advancing the cutoff with delay |
> | **DQ-19** | AV-2 — a CQ due before the pause (or a pre-pause request) whose evaluation time falls inside the pause | **no CQ is newly concluded during deactivation; the withdrawal is admitted at step 4; a late evaluation after release creates no decision** (a stated consequence, flagged; resolved within function; not a Human question) | the direction fixes both rules — the cutoff never advances (delay never helps the decision) and no CQ required to remain paused is newly concluded (delay never overrides the pause) — and R1-D13 / HQ-1 let an undecided applicant withdraw while paused; holding the *request* until release would contradict HQ-1's offer; RC_P3 W7b. With receipt as the coordinated check (MP-13 §3 as clarified by layer 12) the case arises only where the pause is serialised ahead of this operation at the ordering point — the direction's own coordination, not processing delay; and where it arises, the delay favours the applicant, never the CQ, which the direction permits. Stated so it is not mistaken for silence |
> | **DQ-20** | AV-2 — the CQ's disposition after an admitted withdrawal | **`cq_close`** (stop collecting; evidence retained) is proposed; **B-G9 (vii) is the binding prevention either way** | members should not be asked to keep deciding a withdrawn application; a DISCUSSION that cannot close does not defeat the guardian; the alternative (let it conclude, unused) is not adopted and would leave an unused result to explain |
> | **DQ-21** | AV-2 — the ordering point's realisation | **an application-keyed transaction-scoped exclusive lock (`pg_advisory_xact_lock` on the application UUID), nested inside §6.5's LO root lock where MP-12's reading holds — root → application** | one serialisation domain for all terminal writers of one application without deciding §6.5's reach; the alternative row lock (`FOR UPDATE` on the application record) is equivalent for a single-row record and stated; root-lock-only depends on §6.5's reach, which A1 is to test |
> | **DQ-22** | AV-2 — MP-13 on a recorded `CSA` route | **steps 2–3 vacuous; steps 1 and 4 apply** | no automatic evaluation exists on a CSA route (the assignee's decision is the completing act); the 27-day trigger produces no decision (R1-D17); the recorded route governs over the current setting |
> | **DQ-23** | AV-2 — a terminal row admitted between the request's receipt and its (delayed) evaluation | **it stands (step 1, CD-13); the withdrawal is refused** (a stated consequence, flagged; resolved within function; not a Human question) | CD-13's "first accepted by the system" is an admission; a received-but-unevaluated request is not a terminal record; the cutoff protects the applicant against the *evaluation* using later evidence, not against an admission that already occurred; MP-13 §3 as clarified by layer 12 makes receipt the coordinated check, with no queued form, so the window is the ordering point's own serialisation (a writer that acquired OP-W ahead of this operation) — the coordination the direction requires, not processing delay; RC_P3 W13. Stated so it is not mistaken for silence |

---

**⟨E2.079⟩** · trace: L12 02_REPLACEMENTS_AND_ADDITIONS_L12.md line 106
· status: **resolved within function — DQ-24 (the knowledge coordinate of step 2 fixed at T_cut; flagged for the Human Governor's sight; DISCUSSION's prompt recording is the requirement — EL-14)**

| **DQ-24** | AV-2 — the knowledge coordinate of MP-13 step 2 | **`known_at = T_cut`: both evaluation coordinates fixed at receipt; the already-due evaluation is the one later-recorded thing that counts, and it is determinate as of a deadline ≤ `T_cut` (resolved within function; a stated consequence, flagged)** | the direction: the cutoff never advances with delay, and delay must not give the CQ time to defeat the withdrawal; §5.3's knowledge dimension is exactly what delay advances, so a rule that admits any later-recorded evidence is delay-dependent — the evaluation cannot see a record that does not yet exist — unless it waits for DISCUSSION, which is giving the CQ time; the exception is delay-invariant only because it is computed as of a fixed instant ≤ `T_cut` from the participation as at that instant (EL-14). Consequence, stated so it is not mistaken for silence: a conclusion DISCUSSION reached before receipt but recorded after it, on a CQ not yet due, does not count — DISCUSSION's prompt recording of conclusions is the requirement this places on the dependency (EL-14). RC_P4 K1–K6 |

---

**⟨E2.084⟩** · trace: L09 03_MECHANISM_PROPOSALS.md lines 13–23
· words replaced from L10 01_REPLACEMENTS_AND_ADDITIONS_L10.md line 83
· words replaced from L11 01_REPLACEMENTS_AND_ADDITIONS_L11.md line 31
· words replaced from L12 02_REPLACEMENTS_AND_ADDITIONS_L12.md line 29
· words replaced from L10 01_REPLACEMENTS_AND_ADDITIONS_L10.md line 86
· words replaced from L11 01_REPLACEMENTS_AND_ADDITIONS_L11.md line 36
· words replaced from L11 01_REPLACEMENTS_AND_ADDITIONS_L11.md line 51
· words replaced from L16 01_REPLACEMENTS_AND_ADDITIONS_L16.md line 150 (RT16-08, MP1-WORDING)
· status: **PROPOSED — layer 9 as corrected by layers 10–12 (MP-1: the derived PAUSED status; step 3a; B-G9 (vi) and (vii); B-G9′ refuses re-engagement while PAUSED; withdrawal evaluated only under MP-13 step 4; resumption without a write; dissolution → UNABLE; CARE's notices APPLICATION_PAUSED / _PAUSED_APPLICANT / _RESUMED / _WITHDRAWN; the hold requirement on DISCUSSION — REQUIRED); DECIDED behind it — R1-D13 (option B: undecided work pauses), HQ-1 (the offer to request withdrawal; the clock), the AV-2 direction; OWED — DISCUSSION's hold (EL-8: unverified), cq_hold/cq_release; LIMIT — [M] RC_P1 family P, RC_P2 H1–H2 only — as corrected by layer 16 (MP-1's `APPLICATION_WITHDRAWN` destination restated in MP-13 §6's words — reconciliation of wording)** — layer 16 (US015-B-5 integration)
· status before layer 16 (preserved): **PROPOSED — layer 9 as corrected by layers 10–12 (MP-1: the derived PAUSED status; step 3a; B-G9 (vi) and (vii); B-G9′ refuses re-engagement while PAUSED; withdrawal evaluated only under MP-13 step 4; resumption without a write; dissolution → UNABLE; CARE's notices APPLICATION_PAUSED / _PAUSED_APPLICANT / _RESUMED / _WITHDRAWN; the hold requirement on DISCUSSION — REQUIRED); DECIDED behind it — R1-D13 (option B: undecided work pauses), HQ-1 (the offer to request withdrawal; the clock), the AV-2 direction; OWED — DISCUSSION's hold (EL-8: unverified), cq_hold/cq_release; LIMIT — [M] RC_P1 family P, RC_P2 H1–H2 only**
· supersedes: L09 03_MECHANISM_PROPOSALS.md line 15; L10 01_REPLACEMENTS_AND_ADDITIONS_L10.md line 83; L10 01_REPLACEMENTS_AND_ADDITIONS_L10.md line 86; L11 01_REPLACEMENTS_AND_ADDITIONS_L11.md line 31

## MP-1 — Application hold and resumption under R1-D13

**Proposal.** *Records.* No stored status. The LO's `→ DORMANT` transition (deliberate, AT-14; or timed at W1) and its `→ ACTIVE` reactivation are the only records; the pause is **derived**: an application is **PAUSED** at `(state_at, known_at)` iff it is in progress (engaged, no terminal row) and the LO folds `DORMANT` at those coordinates. §2A's read order (L7 01 §1 as replaced by RT-36) gains, after step 3 (the current engagement) and before step 4: *3a — if the LO folds `DORMANT` at the coordinates → PAUSED — stop.* Unengaged applications during deactivation read WAITING (RT-31d). *Guardians.* B-G9 gains a term (vi): a decision is refused if the LO folds `DORMANT` at `(NEW.effective_at, NEW.recorded_at)`; **and a term (vii), stated for every route and every caller — the ordinary decision route and any background writer of CQ results alike: a decision is refused if any terminal row (a `core_applicationwithdrawal` row, or a `core_applicationdecision` row — PC-08's `UNIQUE` restated for every route) is on the application's record at `NEW.recorded_at`, whatever that row's or `NEW`'s effective time (W-FIN; CD-13; MP-13 step 1); B-G9 is evaluated only under the ordering point OP-W (MP-13 §3).** B-G9′ refuses re-engagement while PAUSED (the status at `E`'s coordinates is not UNABLE); a new application record is refused while the LO folds `DORMANT` at its coordinates (the existing PF-03 test, `01_AM` §3.2 row PF-03). **Withdrawal (B-G10) is evaluated only as step 4 of MP-13, under OP-W, after steps 1–3 — an undecided applicant may request withdrawal while paused (R1-D13; HQ-1); a valid pre-pause CQ result is recognised at step 2 subject to terminal-record finality (step 1) and to the fixed evaluation cutoff; an open or incomplete CQ does not block the withdrawal.** *Route.* Resumption is automatic in the sense that nothing is written for it: reactivation ends the pause because the fold no longer reads `DORMANT`; the route owner then re-tests completion authority at its own coordinates (Ruling 9) — a CSA assignee whose assignment ended during the pause makes the route UNABLE (§2A step 6) and re-engagement follows; a CQ whose population changed follows DISCUSSION's rule. *Authority changes while paused.* Prior valid decisions stand and their membership effect may be recorded during the pause (the effect names a decision effective before the deactivation; B-G9 is not engaged — the decision already exists). A CQ result concluded before the pause's `effective_at` is **supporting evidence, not a decision** — a CQ result is never itself an admitted application terminal record (the Human Governor's direction of 24 September 2026). The decision record naming it (`core_applicationdecision`, route `CQ`, actor the deciding Organism or Circle, enterer CARE — R1/R3), effective at the result's time, may be written during the pause **only through that authorised route, under B-G9 including term (vii), and only as the first terminal admission for the application**: if a `core_applicationwithdrawal` row or any other terminal row is already on the record, the write is refused (CD-13 / W-FIN; MP-13 step 1) and the result remains a result — it displaces nothing and needs no correction act to be ignored. A withdrawal request received while such a result is on DISCUSSION's record — recorded by the request's `received_at`, MP-13 step 2 (a) — resolves under MP-13 steps 2–3 (the decision is admitted first; the request is refused; CARE explains); a result DISCUSSION records only after the request's `received_at` is not established at receipt and counts only as the already-due evaluation of a CQ due at that time (step 2 (b)). A result that would only be concluded during the pause is not produced (the hold, below); no CQ required to remain paused is newly concluded during LO deactivation. *Deadlines while paused.* MP-6 / HQ-1. *Dissolution.* Paused undecided applications are never decided: at the LO's `→ CLOSED` the route closure makes each UNABLE permanently (§2A step 4); no `DECLINE` is manufactured; the applicant is notified (`APPLICATION_ROUTE_CLOSED`, PC-24, to the applicant's own notice channel — a DISCUSSION interface). *CARE.* On deactivation, one `APPLICATION_PAUSED` notice per in-progress application to the LO board (the deciding Circle's board for Circle routes) **and one `APPLICATION_PAUSED_APPLICANT` notice to the applicant's own notice channel (a DISCUSSION interface, as `APPLICATION_ROUTE_CLOSED`)**, each stating that the application is held, the recorded reason where disclosure is authorised, the recovery deadline (the W2 snapshot), the remaining or overdue position of any assigned completion clock (MP-6), and possible dissolution — facts distinguished from inference; the applicant's notice also offers **the opportunity to request withdrawal (HQ-1): a withdrawal request is the applicant's own attributable act, which CARE facilitates; the request is evaluated together with the application's deciding position under MP-13's four steps, in order — where a valid qualifying CQ result, or an already-due lawful evaluation, establishes the decision first, that decision is admitted through its authorised route and stands, and CARE explains it (`APPLICATION_WITHDRAWAL_NOT_ADMITTED`); where the withdrawal is admitted (B-G10, at step 4), CARE records it and reports to the LO Coordinators (`APPLICATION_WITHDRAWN` to the applicant's own channel and to the LO board (the deciding Circle's board for Circle routes), PC-24 — MP-13 §6's words (layer 16)). The offer says this — an offer to request withdrawal is not a promise that withdrawal remains available after a valid application decision is admitted — and, where at the notice's own coordinates the deciding CQ holds a result eligible under MP-13 step 2, the notice states that a result exists and that the decision naming it is pending admission. CARE never withdraws for the applicant and chooses no outcome.** On resumption, `APPLICATION_RESUMED` to the same audiences, with the remaining or overdue position. *DISCUSSION interface, REQUIRED.* A CQ open at the pause is **held**: `cq_hold(engagement_reference, from = the LO's DORMANT effective_at)` — (i) DISCUSSION suspends the CQ's deadline clock; (ii) no result is produced while held; (iii) no further participation is admitted; (iv) participation already recorded is retained; `cq_release(engagement_reference, at = the reactivation's effective_at)` resumes the clock with its remaining time (stated by analogy to HQ-1's rule for the assigned clock — a requirement PKT-B places on DISCUSSION, not a decision HQ-1 makes about CQ); both are recorded naming the LO transition as their authority. **A DISCUSSION that cannot hold does not meet the requirement; no fallback lets a CQ conclude during the pause** (layer 9's fallback is withdrawn — AV-2; R1-D13 pauses the CQ work itself). The hold extends no assignment authority and no recovery window: the LO's W2 deadline is the LO chain's own snapshot and is untouched.

**Alternatives.** (i) A stored `HELD` token on the application — rejected: it re-introduces the stored status F2-04 withdrew and needs a write on deactivation and another on resumption for every application. (ii) Cancel undecided applications at deactivation — rejected by R1-D13's words ("pauses"; "retain its evidence"). (iii) Let CSA work complete but pause CQ work — rejected: R1-D13 names both.

**Consequences.** A pause is visible everywhere the LO's fold is; nothing is rewritten on resumption; the pause's start and end are the LO chain's own facts, so the paused interval is derivable for MP-6.

**Recommendation `[A]`.** The derived PAUSED status with B-G9 (vi) and B-G9′'s refusal, as proposed.

**Evidence.** [M] probe family P: PAUSED derived at every coordinate inside the deactivation interval and nowhere outside; a decision dated inside the interval refused (P3); a withdrawal inside the interval admitted (P4); re-engagement inside refused (P5); resumption without a write (P6); route closure on dissolution → UNABLE (P7). [T] R1-D13; PF-03; §2A. No [E].

⟦editorial⟧ MP-6 — deadlines (R1-D17) against the pause (R1-D13): layer 9 with its spans as replaced by layer 10 RT10-15 (a)/(b), RT10-16, RT10-17, RT10-18 (AV-4; HQ-1):

---

**⟨E2.085⟩** · trace: L09 03_MECHANISM_PROPOSALS.md lines 73–85
· words replaced from L10 01_REPLACEMENTS_AND_ADDITIONS_L10.md line 111
· words replaced from L10 01_REPLACEMENTS_AND_ADDITIONS_L10.md line 114
· words replaced from L10 01_REPLACEMENTS_AND_ADDITIONS_L10.md line 119
· words replaced from L10 01_REPLACEMENTS_AND_ADDITIONS_L10.md line 124
· words replaced from L10 01_REPLACEMENTS_AND_ADDITIONS_L10.md line 129
· words replaced from L16 01_REPLACEMENTS_AND_ADDITIONS_L16.md line 64 (RT16-04, OVERDUE)
· status: **DECIDED — R1-D17 (the 27-day default; an overdue trigger), HQ-1 (24 Sep 2026: the clock stops during the pause and continues with its remaining time; no reset; an existing overdue condition never erased; no extension of assignment authority; no change to the LO's recovery/dissolution deadline); PROPOSED — layer 9 as corrected by layer 10 (completion_due_at on PF-01's row; the one-form derived condition; APPLICATION_OVERDUE; treatments (ii) and (iii) HISTORY); LIMIT — [M] RC_P2 family T; layer 9's family D HISTORY; no [E] — as corrected by layer 16 (the notice's identity, schedule, destination and route at (g″) — VC-authored, layer 16; HQ-1 and R1-D17 unchanged)** — layer 16 (US015-B-5 integration)
· status before layer 16 (preserved): **DECIDED — R1-D17 (the 27-day default; an overdue trigger), HQ-1 (24 Sep 2026: the clock stops during the pause and continues with its remaining time; no reset; an existing overdue condition never erased; no extension of assignment authority; no change to the LO's recovery/dissolution deadline); PROPOSED — layer 9 as corrected by layer 10 (completion_due_at on PF-01's row; the one-form derived condition; APPLICATION_OVERDUE; treatments (ii) and (iii) HISTORY); LIMIT — [M] RC_P2 family T; layer 9's family D HISTORY; no [E]**
· supersedes: L09 03_MECHANISM_PROPOSALS.md line 75; L09 03_MECHANISM_PROPOSALS.md line 79; L09 03_MECHANISM_PROPOSALS.md line 83; L09 03_MECHANISM_PROPOSALS.md line 85

## MP-6 — Deadlines (R1-D17) against the pause (R1-D13)

**Proposal.** *Records.* PF-01's assignment row gains `completion_due_at` = `effective_at` + the route's configured days (27 free default), snapshotted at assignment; on lapse existing values stand and new assignments use 27. *Derived condition — one form.* `allowance = completion_due_at − effective_at` (recorded at assignment); `elapsed(now) = (now − effective_at) − Σ DORMANT time of the LO within [effective_at, now)`; `remaining(now) = allowance − elapsed(now)`; **OVERDUE iff `elapsed(now) > allowance` and no decision effective by `now`** — equivalently `now > completion_due_at + Σ DORMANT time`, the adjusted absolute deadline CARE reports. Nothing recorded changes; the paused intervals are the LO chain's own facts. *CARE.* `APPLICATION_OVERDUE` (PC-24; its identity, schedule, destination and restricted-board route at (g″), layer 16) to the deciding board at the due instant, again at each subsequent 7 days while overdue; it supports reassignment (PF-02: an explicit `ENDED` on the assignment plus a new one by the LO/Circle Coordinator). *Authority.* The deadline expires nothing: B-G9 (iii) still admits the assignee's decision while the assignment folds `ACTIVE`; a decision after the assignee's office ended needs a new assignment. *Waiting time* before assignment is exposed by CARE, no deadline. **The interaction with the pause is decided (HQ-1, 24 Sep 2026): the clock stops during an R1-D13 pause and, on lawful resumption, continues with its previously remaining time under the arrangement recorded at assignment — treatment (i), whose layer-9 expression compared a point in time with a length of time (AV-4) and is corrected here; (ii) and (iii) are history:**

| treatment | rule | practical consequence |
|---|---|---|
| (i) the clock stops — **decided (HQ-1)** | OVERDUE iff the elapsed unpaused time since assignment exceeds the recorded allowance: `(now − effective_at) − Σ paused > completion_due_at − effective_at`, i.e. `now − Σ paused > completion_due_at`; the paused intervals are the LO chain's `DORMANT` intervals within `[effective_at, now)`, so nothing is rewritten; repeated pauses accumulate; an overdue condition existing at the pause is frozen for its duration and never erased; remaining time and the adjusted deadline are derived, not stored | the assignee is never flagged for time the LO itself paused; the allowance is never reset; CARE states the remaining or overdue position at the pause and at resumption (MP-1); the pause extends no assignment authority — B-G9 (iii) is re-tested at the decision's own coordinates, and an assignment that ended during the pause makes the route UNABLE at resumption — and alters no K-2 recovery or dissolution deadline; costs one fold over the LO chain per overdue check |
| (ii) fresh deadline on resumption | at reactivation, OVERDUE is derived against `reactivation + configured days` | simple; but a nearly-overdue assignment gains a full new period, and the recorded `completion_due_at` no longer describes anything |
| (iii) the deadline stands | OVERDUE fires on the calendar regardless of the pause; CARE explains the pause | nothing derived; but every assignee of a deactivated LO is flagged at resumption for delay they could not avoid, which the overdue trigger then mis-describes |

**Decided (HQ-1).** Treatment (i). Nothing remains to recommend: the elapsed-time calculation, the interval handling (repeated pauses; a pause spanning an existing overdue condition), the preserved assignment snapshot, the overdue treatment, the resumption authority check and the notice contract are design obligations discharged in the text above and MP-1 as corrected, and modelled at RC_P2 family T.

**Evidence.** [M] RC_P2 family T (layer 10): T1 — assigned 10, due 37, paused 20–50, no decision: elapsed 18 at 58, remaining 9, adjusted deadline 67, first overdue day 68, and the overdue test equal to `now > adjusted deadline` at every sampled day; the written layer-9 expression fires at 58 (AV-4); T2 — two pauses, elapsed frozen at 20 through the second, first overdue day 63; T3 — overdue from 38, remaining frozen at −3 through a pause 40–60, −4 at 61 — the pause erases nothing; T4 — the layer-9 scenario's decision at 60 hid the deadline; T5 — an assignment ended during the pause cannot decide at resumption; T6 — assigned one day before the pause, first overdue day 77. **Layer 9's family D is history: its scenario placed a decision at 60 that hid the adjusted deadline, so the written formula was never exercised; its raw output is preserved byte for byte.** [T] R1-D17; HQ-1. No [E].

⟦editorial⟧ The ILC placement of the surviving records under CD-6, as recorded by layer 6 03_AM (tables that stand) and layer 7 02_FA_D2 §2/§4 — CD-6 DECIDED (a), 24 Sep 2026; the (b) columns are HISTORY (RT-18, RT-19):

---

**⟨E2.088⟩** · trace: L06 03_AM_ILC_PLACEMENT.md lines 83–92
· status: **PROPOSED — layer 6 (§1.3 the predecessor link — a column of the application record, the applicant's act, governs nothing, read under the access rules; not the ILC's chain-predecessor cell); OPEN — AF-3 (2): which object owns an application record and whose membership its audience derives from (before any read path through the link is implemented)**

### 1.3 The predecessor link — `predecessor_reference NULL` on the application record (`01_AM` §3.1, §3.3)

| requirement | placement | reason |
|---|---|---|
| where it sits | **a column of the application record** (PC-08's subject) — a durable reference from the new application to its withdrawn predecessor; no relation is added | `01_AM` §3.1; the Human decision's words ("linked to its predecessor") |
| whose act | **the applicant's** — the new application is the Learner's act, recorded with the Learner as actor and as enterer (Ruling 3: the actor is the party whose decision the act carries out); the reference is named in that act | naming a predecessor is a component of the applicant's submission, tested by the submission checks afresh (`01_AM` §3.2) |
| what it governs | **nothing** — read by no guardian and no status read (`01_AM` §3.3, W3-*) | the decision's words ("inheriting nothing") |
| four-time grammar | the application record's own four-time columns (correction 04 §E2-Q2 (a): "append-only, four-time grammar") — the link has no time of its own | it is a column, not a row |
| access | **read only under the applicable access rules**: the link is a component of the new record and carries that record's audience and posture; resolving it to the predecessor's *content* is governed by the predecessor record's own audience. Where a reader reaches the new record but not the predecessor, the reference is known to exist and to be of its kind (the same shape as the ILC's existence-with-kind) and is not opened | the access model (audience follows the record's own object; a later act changes no earlier record's audience) is not altered here; **which object owns an application record — and therefore whose membership its audience derives from — is not stated by the standing text and is not decided here** (it is not required by either decision) |
| the ILC's own "predecessor" cell | **not that cell.** The ILC's predecessor is the chain cell of §5.1 (the immediately preceding event, unique). The application's `predecessor_reference` is a *subject-level* reference between two application records, both of which are submissions, not chain events | naming observation, §3 |

---

**⟨E2.089⟩** · trace: L06 03_AM_ILC_PLACEMENT.md lines 109–112
· words replaced from L07 02_FA_D2_CD6_PLACEMENT.md lines 175–176
· status: **PROPOSED — layer 6 (§1.4 the withdrawal relation unchanged; placed as the application's other rows); the parenthesis replaced per layer 7 §8(b) (L07 02 lines 175–176) — VC assembly change F-5 (Reviewer fidelity return)**
· supersedes: L06 03_AM_ILC_PLACEMENT.md line 112
· note ⟦editorial⟧: VC assembly change F-5: layer 7 §8(b)'s existing replacement words applied as written, with their source line wrap and emphasis; no new words.

### 1.4 `core_applicationwithdrawal` and B-G10 — not new records; stated for completeness

The withdrawal relation is unchanged (correction 04 §E2-Q2 (a), `UNIQUE (application_id)`): a plain append-only relation carrying the
four-time grammar, the applicant as actor, placed as the application's other rows are **(§1.3's table applies
  to it under CD-6 (a) and (b) alike; its placement under each is `02_FA_D2` §2's withdrawal row)**.

---

**⟨E2.093⟩** · trace: L07 02_FA_D2_CD6_PLACEMENT.md lines 141–143
· status: **PROPOSED — layer 7 (B-G10 is a guardian, not a record — terms (a), (b), (d) at the row's own coordinates, term (c) a test of what is on the record at that insert)**
· supersedes: L06 03_AM_ILC_PLACEMENT.md lines 113–114

> **B-G10 is a guardian, not a record**: an admission guardian evaluated on the insert of a withdrawal row — terms (a), (b) and (d) at
> that row's own coordinates, term (c) as a test of what is on the application's record at that insert (`01_AM` §4 as corrected by
> `01_FA_D1` §7) — exactly as B-G9 and B-G9′ are evaluated on the rows they admit.

---

**⟨E2.094⟩** · trace: L07 02_FA_D2_CD6_PLACEMENT.md lines 84–101
· status: **DECIDED — CD-6 reading (a): every surviving record and the chain-free trio stand OUTSIDE §5.4 (the (a) column is the live placement); the (b) column and its 'unwritten provision' costing are HISTORY (RT-18, RT-19 — preserved as the record of what was weighed); the 'intermediate state' column — no ruling requires one (CD-6's fall-back dormant)**
· supersedes: L06 03_AM_ILC_PLACEMENT.md lines 94–104

## 2. Each surviving record placed under (a) and under (b)

The records are those the commission names, plus the two the Reviewer names by name (engagement, decision). "Sits with" names the
chain-free design of the same shape, so the placement of PKT-B's own rows is coherent with the trio's. "Intermediate state" is the
account's fall-back test.

| record | under **(a)** — §5.4 governs the §5.1 chain contract | under **(b)** — §5.4 governs any relation read at an effective time | would (b) require an AMEND provision, and which | an intermediate state a chain-free design cannot carry? |
|---|---|---|---|---|
| **the application record** (PC-08's subject), with `predecessor_reference NULL` | **OUTSIDE.** No head pointer, no transition function, no stored vocabulary token, no §5.1 commit guardian: a submission row read for existence and eligibility (§2A step 0). **Sits with A-12's registration row** — one act row, "eligible at the act's coordinates". The link is a column of it, read by no guardian and no status read (W3); no placement of its own. Correction `04` §E2-Q2 (a)'s sentence "no state column and no fold, so no §5.4 question arises" is *true under (a)* | **INSIDE the words.** §2A step 0 reads it at `(state_at, known_at)` with §5.2's eligibility test; B-G9, B-G9′ and B-G10 read it at the candidate's coordinates. The same sentence of correction `04` is *false under (b)*, and 03_AM §1.3 did not say so; it is said here | **Yes — and none of AMEND-A, AMEND-B or AMEND-C covers it.** A further AMEND row on §5.4 would be needed (the application as an aggregate: this record the anchor; the engagement, decision and withdrawal rows its chain; a head pointer; a closed vocabulary; a commit guardian; §5.7 item 1's count and §11.1's "twelve chains" amended) — or an explicit exception stated in §5.1's terms. **Neither is written, and neither is proposed here** | **None required by any issued decision.** Submitted / engaged / decided / withdrawn are row-existence facts. PENDING, UNABLE, COMPLETABLE and HELD are derived from *other domains'* state (DISCUSSION's CQ status; SERVICE's assignment fold; a route closure) and could not be tokens of this record's own chain anyway — a fold over the application's chain cannot see them. W-FIN's mutual exclusion is a guardian over row existence, needing no chain. The A-11-shaped cost would arise only if a ruling ever required a *stored* application state (a "paused" application, say); none does |
| **the engagement rows** (`core_applicationengagement`, `UNIQUE (application_id, engagement_ordinal)`) | **OUTSIDE on all four elements.** No head pointer (the current engagement is the greatest eligible ordinal — derived, §2A step 3); no transition function; no state vocabulary (a row carries route and party, not a state); no §5.1 guardian (B-G9′ validates one insert against the eligible set, not anchor, chain and head together). **The resemblance is stated:** ordinals are consecutive and a re-engagement's `basis_reference` names the prior engagement's UNABLE record — sequence and reference, which A-11 has not. Of the surviving records this is the one alternative (a) tests hardest; on its four named elements it is outside. **Sits with A-11's admission rows** (several per subject, each read by eligibility) | **INSIDE.** Read at an effective time by §2A step 3 and by B-G9's "current engagement" at `NEW`'s coordinates | **Yes — the same unwritten provision as the application record** (the engagement rows would be events of that chain) | **No.** A chain of engagements would carry ENGAGED(*n*) events; UNABLE is not the engagement's state but a read of the route owner's record, and would stay derived |
| **the decision row** (`core_applicationdecision`, PC-08, unique per application) | **OUTSIDE.** One row per application, no chain; **sits with A-12's registration row** (a single act row read at coordinates) | **INSIDE.** Read at an effective time (§2A step 1–2). W-FIN's existence test at record time is not an effective-time read; the status read is | **Yes — the same unwritten provision** | **No** |
| **the withdrawal row** (`core_applicationwithdrawal`, `UNIQUE (application_id)`) | **OUTSIDE.** As the decision row; **sits with A-11's ending row** `core_circleparticipationend` (`participation_id UNIQUE`) — the same shape exactly: one terminal row per subject, read by eligibility, admitted by a guardian on its own insert (B-G10) | **INSIDE.** Read at an effective time (§2A step 1–2) | **Yes — the same unwritten provision** | **No** |
| **`predecessor_reference`** (a column of the application record) | not a relation; no placement of its own under (a): it is folded by nothing and read at coordinates by nothing (W3-a…h; `01_AM` §3.3) | rides with its record; the column itself is still read by no §5.2-style query | with its record | n/a |
| **`core_carenoticeintent`** (kept, `02_AM` §1.2) | **OUTSIDE.** A plain process relation: no head, no fold, no vocabulary, no guardian; enterer the CARE system principal (03_AM §1.1's table stands) | **OUTSIDE on (b)'s words too.** It is read *by key* by the CARE runner for the exact keys handed out, never at `(state_at, known_at)`. `due_at` is a key value naming the instant the notice is about (FC-D2 §1.2), not the row's `effective_at`; the intent carries a database-assigned `recorded_at` and no effective time. **Stated, not decided:** if "read at an effective time" were stretched to the runner's scan for intents whose `due_at` has arrived, (b) would reach it and an explicit exception would be the route; on (b)'s words as recorded it is outside | **No** on (b)'s words; an explicit exception only on the stretched reading | **No** |
| **`core_carenotice`** (the delivery evidence, O-3 (d)) | **OUTSIDE.** A plain evidence relation carrying the four-time grammar; no head, no fold, no vocabulary, no guardian (03_AM §1.2's table stands) | **INSIDE.** It carries `effective_at` — the posting instant DISCUSSION confirms — and its reads are coordinate-bound: C-3's evidence requirement is answered by presenting what had been posted as at `(state_at, known_at)`, which is §5.2's eligibility test applied to its rows | **Yes — and none of AMEND-A, AMEND-B or AMEND-C covers it.** Either an AMEND row making delivery evidence an aggregate (a one-event chain per notice — a chain with nothing to fold) or an explicit exception. **Neither is written, and neither is proposed here** | **No** |
| **A-11** — Circle participation without a chain (correction `02` §C-4) | **OUTSIDE** — the design that gives (a) its wording: admission row and ending row, a set-difference read on §5.2's eligibility predicate, no head, no state token, no transition function, no fold | **INSIDE.** "Participant of Circle C at `(state_at, known_at)`" is a read at an effective time | **Yes — AMEND-A** (Circle membership as the thirteenth aggregate; PC-11 the chain; §5.7 item 1 and §11.1 amended; vocabulary to §5.5; B-G5 to §6.1) | **Its stated cost:** a suspended Circle participant would need a chain. No issued ruling requires one: K-2's cascade is an ending row under a PC-14 rule. The cost is real and currently unexercised |
| **A-12** — Learner origin without a chain | **OUTSIDE** for registration: a single act row, "eligible at the act's `effective_at` as known at record time". Account access (`DEACTIVATED` / `REACTIVATED`) is not a PKT-B relation under A-12: it is an interface owned by the Platform Security and Operations workstream (H-7), `account_active(identity, at)` | **INSIDE** for the registration row (read at an effective time by B-G2's Learner limb). Account access stays outside PKT-B's §5.4 by delegation — the delegation, not the reading, is what keeps it out under (b); the state it reads over time lives in the workstream's record | **Yes — AMEND-B** (Learner as an aggregate: registration origin and account access; vocabulary `REGISTERED / DEACTIVATED / REACTIVATED`; B-G2's Learner limb) for the registration row; AMEND-B also if account access is to be a PKT-B chain rather than the interface | **Account access is a state read over time and A-12 carries that cost by delegation**, not by a chain of its own; registration has no intermediate state. HD-C4 (PC-12 versus a creation instant) remains Human |
| **A-15** — entitlement as interval facts | **OUTSIDE.** Grant rows and end rows; "entitled at `(state_at, known_at)`" derived; no head, no fold; `effective_until` a column of a support relation, so §5.3 unamended | **INSIDE.** "Entitled at `(state_at, known_at)`" is a read at an effective time | **Yes — AMEND-C** (Entitlement as an aggregate; PC-15; vocabulary `GRANTED / EXTENDED / GRACE_* / ENDED / REPLACED`; PC-15 guardians) **plus AMEND-IF PC-15** — §5.3's four-time grammar amended for PB-11's fifth time, which A-15 avoids | **No.** `GRANTED / EXTENDED / GRACE_* / ENDED / REPLACED` are all derived; trial-to-paid is two rows in one transaction; grace is a derived window. Its `LD-D1` interaction (a term read by the clock under H; lapse rows under B) is stated in §C-4 and not decided |

---

**⟨E2.095⟩** · trace: L07 02_FA_D2_CD6_PLACEMENT.md lines 127–137
· status: **DECIDED — CD-6 (a) (the summary table's '(a)' column live; '(b)' columns HISTORY); the removals of core_applicationresubmission and core_carenoticecancellation — the 23 Sep decisions (1), (2)**
· supersedes: L06 03_AM_ILC_PLACEMENT.md lines 117–126

> | record | survives | placement (03_AM §1 tables stand) | under CD-6 (a) | under CD-6 (b) | (b)'s provision |
> |---|---|---|---|---|---|
> | `core_applicationresubmission` | **removed** (decision 1) | none | — | — | — |
> | `core_carenoticecancellation` | **removed** (decision 2) | none | — | — | — |
> | the application record, with `predecessor_reference` | column new | plain append-only relation, four-time grammar; the link the applicant's act, governs nothing, read under the access rules; **not** the ILC's chain-predecessor cell | outside (sits with A-12's registration row) | inside | **unwritten** — a further AMEND row or an explicit exception |
> | `core_applicationengagement` | unchanged | plain append-only relation; ordinals and UNABLE basis are sequence and reference, not §5.1's elements | outside (sits with A-11's admission rows; the hardest case for (a)) | inside | the same unwritten provision |
> | `core_applicationdecision` | unchanged | plain, one per application | outside (sits with A-12's registration row) | inside | the same |
> | `core_applicationwithdrawal` | unchanged | plain, one per application; admitted by B-G10 on its insert | outside (sits with A-11's ending row) | inside | the same |
> | `core_carenoticeintent` | kept | plain process relation; not a governed event; enterer the CARE system principal | outside | **outside** on (b)'s words (read by key, not at an effective time); inside only on a stretched reading | none on (b)'s words; an explicit exception on the stretched reading |
> | `core_carenotice` | unchanged (O-3) | plain evidence relation carrying C-3's requirement and the four-time grammar; key time via `s015_canonical_ts` | outside | inside | **unwritten** — an AMEND row or an explicit exception |
> | A-11 / A-12 / A-15 | proposed (correction `02` §C-4) | chain-free designs | outside — the source of (a)'s wording | inside | AMEND-A / AMEND-B (+ interface) / AMEND-C (+ AMEND-IF PC-15) — written, before the Human Governor |

---

**⟨E2.123⟩** · trace: L01 LD_RETURN_BLOCK_E2_UNIT_4_MEMBERSHIP_ROUTES_RECOVERY_DISSOLUTION_v1_0.md lines 81–84
· status: **PROPOSED — layer 1 (table header)**

## 8. HUMAN DECISIONS SURFACED BY THIS BLOCK

| # | decision | recommendation `[A]` |
|---|---|---|

---

**⟨E2.124⟩** · trace: L01 LD_RETURN_BLOCK_E2_UNIT_4_MEMBERSHIP_ROUTES_RECOVERY_DISSOLUTION_v1_0.md line 85
· status: **PROPOSED — layer 1 (PF-02 default No — the recommendation [A] not adopted; C-1: a missing setting is No)**

| PF-02 | the sole-participant delegation setting's default | `No`; alternative route R2 with an explicit transfer |

---

**⟨E2.125⟩** · trace: L01 LD_RETURN_BLOCK_E2_UNIT_4_MEMBERSHIP_ROUTES_RECOVERY_DISSOLUTION_v1_0.md line 86
· status: **DECIDED — R1-D13 (PF-03 as replaced by RT-31a: undecided work pauses)**

| PF-03 | Deactivated's restrictions | as §2.3 |

---

**⟨E2.126⟩** · trace: L01 LD_RETURN_BLOCK_E2_UNIT_4_MEMBERSHIP_ROUTES_RECOVERY_DISSOLUTION_v1_0.md line 87
· replaced in full by L09 02_REPLACEMENTS_AND_ADDITIONS.md line 224
· status: **DECIDED — R1-D16 (HD-F1 contrary; PF-06 withdrawn)**
· supersedes: L01 LD_RETURN_BLOCK_E2_UNIT_4_MEMBERSHIP_ROUTES_RECOVERY_DISSOLUTION_v1_0.md line 87

| HD-F1 | decided contrary (R1-D16): no automatic root membership at registration or reactivation; PF-06 withdrawn (AT-16) | — |

---

**⟨E2.127⟩** · trace: L01 LD_RETURN_BLOCK_E2_UNIT_4_MEMBERSHIP_ROUTES_RECOVERY_DISSOLUTION_v1_0.md line 88
· status: **DECIDED — HD-C2: R3 (CD-3) — the row's recommendation is now the decision**

| HD-C2 | rejoining representation (with C) | R3 |

---

**⟨E2.128⟩** · trace: L01 LD_RETURN_BLOCK_E2_UNIT_4_MEMBERSHIP_ROUTES_RECOVERY_DISSOLUTION_v1_0.md line 89
· status: **DECIDED — HD-E2 (R1-D13), HD-E9 (R1-D17)**

| HD-E2, HD-E9 | with E1 | as there |

---

**⟨E2.129⟩** · trace: L01 LD_RETURN_BLOCK_E2_UNIT_4_MEMBERSHIP_ROUTES_RECOVERY_DISSOLUTION_v1_0.md line 90
· status: **OPEN — LD-Q3 residue (whether a correction act may carry an effective time reaching back into a restriction; end of Unit 5); the O2 prospective exit DECIDED (CD-2, AT-04)**

| `LD-Q3` | shown at §2.2 (prospective exit under O2) — need-by unchanged | O2, unadopted |

---

**⟨E2.130⟩** · trace: L01 LD_RETURN_BLOCK_E2_UNIT_4_MEMBERSHIP_ROUTES_RECOVERY_DISSOLUTION_v1_0.md line 91
· replaced in full by L09 02_REPLACEMENTS_AND_ADDITIONS.md line 284
· status: **DECIDED — CD-1 (LD-Q5: K-2's windows never toll; an office's fixed term does not toll by default; a lawful subscriber setting may provide otherwise — AT-02, AT-03)**
· supersedes: L01 LD_RETURN_BLOCK_E2_UNIT_4_MEMBERSHIP_ROUTES_RECOVERY_DISSOLUTION_v1_0.md line 91

| `LD-Q5` | decided (CD-1): K-2's windows never toll (H-4); an office's fixed term does not toll by default; a lawful subscriber setting `suspension_tolls_term`, snapshotted on the appointment, may provide otherwise (AT-02, AT-03) | — |

---

**⟨E2.131⟩** · trace: L01 LD_RETURN_BLOCK_E2_UNIT_4_MEMBERSHIP_ROUTES_RECOVERY_DISSOLUTION_v1_0.md line 92
· status: **PROPOSED — layer 1 (adoptions PF-01…PF-08 — PF-06 withdrawn (R1-D16); none adopted)**

| adoptions | PF-01, -02, -03, -04, -05, -06, -07, -08 | returned, none adopted |

## ⟦editorial⟧ E2.9 §9 — Authority boundary

---

**⟨E2.132⟩** · trace: L01 LD_RETURN_BLOCK_E2_UNIT_4_MEMBERSHIP_ROUTES_RECOVERY_DISSOLUTION_v1_0.md lines 94–96
· status: **PROPOSED — layer 1 (authority boundary; the controlled-clock test designed, not run; RV-13 stands)**

## 9. AUTHORITY BOUNDARY

Designs PKT-B's side of every route, sequence and interface; designs no CQ lifecycle, CSA workflow, notice content, timed-event infrastructure, disclosure, privacy, export packaging or security work; adopts nothing; decides nothing; the controlled-clock test is designed and not run. Final authority remains with Carmian Owen, Human Governor.
