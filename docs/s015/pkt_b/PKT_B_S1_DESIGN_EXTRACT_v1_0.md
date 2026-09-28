# PKT-B SLICE 1 — DESIGN EXTRACT — for the Copilot Making Engine

Extracted verbatim by US015-B-7 (Vision Chamber), 28 September 2026, from the implementation baseline's operative reading: `CANDIDATE_v1_27_OPERATIVE_READING.md`, 1,282,633 B, SHA-256 `07d629f7344d165f0962639a447a171cb44b3a7ade237e1b188a21fff356ffef` (inside candidate v1.27, `839cfb4e…8a1d`). Each paragraph is reproduced whole, status lines included. **Status words matter:** DECIDED is binding; PROPOSED is the design as it stands; HISTORY is not operative. Where this extract and the reading differ, the reading governs.

Paragraphs: ⟨B.049⟩ membership edges; ⟨B.050⟩ contextual-role edges (context for slice 2); ⟨E2.112⟩ ENDED terminal for the period; ⟨E2.114⟩ probation (edges only in slice 1); Unit 2 §3 (⟨C.037⟩, ⟨C.038⟩, ⟨C.039⟩, ⟨C.040⟩) — membership and office as separate facts; counting; Circle membership requires LO membership.

---

**⟨B.049⟩** · trace: L09 02_REPLACEMENTS_AND_ADDITIONS.md line 69
· words replaced from L10 01_REPLACEMENTS_AND_ADDITIONS_L10.md line 31
· words replaced from U5 03_SUBSTITUTIONS_v1_1.json (refinement return) entry line 6 (RT-U5-06a, R-MEM)
· words replaced from U5 03_SUBSTITUTIONS_v1_1.json (refinement return) entry line 7 (RT-U5-06b, REM)
· status: **DECIDED — CD-3 (R3; ENDED terminal for the period; ENDED → PROPOSED opens the next period), CD-4 (PROPOSED → ENDED), R1-D14 (PROPOSED → PROBATIONARY), HQ-2 (the probation outcome endings); MP-9, MP-11 PROPOSED — layers 9–10; CD-9 (PRIVACY_TRANSFORMED never produced by severance); DQ-4 OPEN; RESTRICTED's meaning is Unit 5's interface — as corrected by Unit 5 (U5 design and refinement, Lead Designer; RT-U5 entries marked above)** — layer 20 (US015-B-5 integration)
· status before layer 20 (preserved): **DECIDED — CD-3 (R3; ENDED terminal for the period; ENDED → PROPOSED opens the next period), CD-4 (PROPOSED → ENDED), R1-D14 (PROPOSED → PROBATIONARY), HQ-2 (the probation outcome endings); MP-9, MP-11 PROPOSED — layers 9–10; CD-9 (PRIVACY_TRANSFORMED never produced by severance); DQ-4 OPEN; RESTRICTED's meaning is Unit 5's interface**
· supersedes: L01 LD_RETURN_BLOCK_B_CLAUSE_MAP_AND_PROPOSED_SPECIFICATION_v1_0.md line 228; L09 02_REPLACEMENTS_AND_ADDITIONS.md line 69

> | **membership** | `PROPOSED → ACTIVE` (admission; founding-invitation consent; the founding batch — CD-5) · `PROPOSED → PROBATIONARY` (admission with probation commencing — R1-D14) · `PROBATIONARY → ACTIVE` (probation completed — MP-9) · `PROPOSED → ENDED` (decline; the Learner's withdrawal from the proposed membership; the dissolution and account-deactivation cascades — CD-4) · `ACTIVE → ENDED` and `PROBATIONARY → ENDED` (departure U-3; deactivation-driven departure U-3; cascade on dissolution Ruling 4; removal through the LO's removal authority — ⟨U5.010⟩ (REM)) · `PROBATIONARY → ENDED` (the probation outcome — a qualifying No, or the three-CQ limit: HQ-2, MP-9 as corrected by layer 10) · `ENDED → PROPOSED` (opening the next period only — CD-3) | **`ENDED` terminal for the period; the only edge out of `ENDED` is `PROPOSED` opening the next period (R3, CD-3)** | **the fresh-period route on the same aggregate (CD-3); the shipped `s015_identity_organism_membership_uniq` stands** | PKT-B | `SUSPENDED`, `RESTRICTED` — shipped tokens, unused by any PKT-B command in v1.0 (MP-11; `RESTRICTED` has no membership-level meaning in v1.0 — ⟨U5.009⟩ (R-MEM); its edges withheld with `SUSPENDED`'s); `PRIVACY_TRANSFORMED` — shipped token, never produced by severance (CD-9), unused in v1.0 |

---

**⟨B.050⟩** · trace: L01 LD_RETURN_BLOCK_B_CLAUSE_MAP_AND_PROPOSED_SPECIFICATION_v1_0.md line 229
· words replaced from L09 02_REPLACEMENTS_AND_ADDITIONS.md line 299
· status: **DECIDED — CD-1 (role suspension: subscriber capability on a constitutional basis; no tolling by default; AT-02/AT-03); K-3; U21g-6**
· supersedes: L01 LD_RETURN_BLOCK_B_CLAUSE_MAP_AND_PROPOSED_SPECIFICATION_v1_0.md line 229

| **contextual-role assignment** | `PROPOSED → ACTIVE` (appointment, an explicit act — K-3; the governing term and the tolling setting in force snapshotted on the appointment, AT-02) · `ACTIVE → ENDED` (END; REASSIGN leaves `ENDED` `U21g-6`; loss of office on departure/deactivation U-3; instrument ending Ruling 4; the recorded term's end under H, tolled only by a lawful setting) · `ACTIVE ⇄ SUSPENDED` (**role suspension — a subscriber capability exercised only on an established constitutional basis by an authorised decision; CARE assists and creates no authority; an absent basis needs a valid amendment first; suspension never implies membership or Circle-participation suspension — CD-1, AT-03**) · `PROBATIONARY → ACTIVE` | `ENDED` terminal | a fresh appointment is a new assignment anchor (no uniqueness constraint blocks it `[R]`) | PKT-B | founder protection during birthing as a refusal on `ACTIVE → ENDED` by a later-appointed Coordinator (E1 item 3); `PRIVACY_TRANSFORMED` (RQ-2) |

---

**⟨E2.112⟩** · trace: L02 04_BLOCK_E2_REVISED_SECTIONS.md line 50
· words replaced from L09 02_REPLACEMENTS_AND_ADDITIONS.md line 59
· status: **DECIDED — CD-3 (ENDED terminal for the period; R3; ENDED → PROPOSED an established edge); B-G6 R3 form — PROPOSED (layer 4 01_O1 (c)); the closing write — the ENDED transition naming the period (layer 2 05 §3.4)**
· supersedes: L01 LD_RETURN_BLOCK_E2_UNIT_4_MEMBERSHIP_ROUTES_RECOVERY_DISSOLUTION_v1_0.md line 67; L02 04_BLOCK_E2_REVISED_SECTIONS.md line 50

**Replacement text.** *`ENDED` is terminal **for the period** under representations R1 and R3 and **for the anchor** under R2 (05_R1_R2_R3_SPECIFICATIONS.md §§1.1, 2.1, 3.1). Rejoining is the expressly allowed fresh route that v0.13 §11.1 leaves to PKT-B: a fresh period on the same aggregate — R3, decided (CD-3); the reading that §11.1's "expressly allowed fresh-aggregate route" is a fresh period on the same aggregate is decided with it, and `ENDED → PROPOSED` (opening the next period) is an established membership edge (Block B §4 as replaced by RT-10). R1 and R2 are history. B-G6 (proposed, R3 form): refuses a `PROPOSED` while a period is open — open meaning a period row that no `ENDED` transition names — and refuses every edge out of `ENDED` other than a `PROPOSED` naming a fresh period row of the next ordinal. The closing write is the `ENDED` transition naming the period. "A later `PROPOSED` opens period 2" now reads: a `core_membershipperiod` row for ordinal 2 is inserted and the `PROPOSED` names it.*

---

**⟨E2.114⟩** · trace: L09 02_REPLACEMENTS_AND_ADDITIONS.md line 348
· words replaced from L10 01_REPLACEMENTS_AND_ADDITIONS_L10.md line 64
· words replaced from L10 01_REPLACEMENTS_AND_ADDITIONS_L10.md line 67
· words replaced from L10 01_REPLACEMENTS_AND_ADDITIONS_L10.md line 70
· words replaced from L14 01_REPLACEMENTS_AND_ADDITIONS_L14.md line 24 (RT14-01, A1-D01)
· words replaced from L14 01_REPLACEMENTS_AND_ADDITIONS_L14.md line 38 (RT14-02, A1-D01)
· status: **DECIDED — R1-D14, HQ-2 as before; PROPOSED — layers 9–10 as corrected by layer 14 (the CQ-route commencement carries no fixed term; the qualification deadline is content; the old-flag mapping of Probation_end by route)** — layer 14 (US015-B-5 integration)
· status at candidate v1.2 (preserved; policies it names DECIDED remain so): **DECIDED — R1-D14 (probation as a subscriber capability; PROBATIONARY counts for K-2; rights separately governed), HQ-2 (24 Sep 2026: a qualifying No ends the membership prospectively; an incomplete CQ opens the next, the probation extended; three CQs at most; the third incomplete ends it without a manufactured vote; CARE informs the LO; the automatic route stands; no Coordinator substitution while a completion CQ is open); PROPOSED — layers 9–10 (the settings, the commencement content, the episode relation — MP-9); the old flag mapping — source observation; existing probations complete after lapse (MP-7)**
· layer 14 displaces: L09 02_REPLACEMENTS_AND_ADDITIONS.md line 348 as replaced by L10 01_REPLACEMENTS_AND_ADDITIONS_L10.md lines 64, 67, 70 — the two spans quoted at RT14-01/02
· supersedes: L09 02_REPLACEMENTS_AND_ADDITIONS.md line 348

> **Probation (R1-D14) — MP-9 in outline.** Probation is a subscriber capability (PC-15 capability `PROBATION`; `MENTORSHIP` beside it): PE-08 gains `probation_enabled` (default No), `probation_duration_days`, `probation_completion_route ∈ {AUTOMATIC, CQ}`. An admission under probation opens the period at `PROPOSED → PROBATIONARY`, whose transition content records the duration, the route and the outcome and retry arrangement (`PROBATION_RULES@<version>`) in force at commencement (snapshot) — the term the member consents to, which CARE's commencement explanation exposes with the three-CQ limit and each outcome's consequence; under `AUTOMATIC` the commencement carries `effective_until` = commencement + duration, `FIXED`, and at the term's end a timed composed `PROBATIONARY → ACTIVE` (actor `NONE`, the probation rule) confers anew; under `CQ` the commencement carries no fixed term (`effective_until_state = NONE`) and records the qualification deadline `qualification_deadline_at` = commencement + duration in its content — a deadline at which qualification opens, not a term of conferral that ends (layer 14, A1-D01) — and reaching that deadline opens the probation-completion CQ through DISCUSSION and grants nothing; the membership stays `PROBATIONARY` while qualification runs; a qualifying Yes → `→ ACTIVE` naming the result; a qualifying No → `→ ENDED`, prospectively; an incomplete first or second CQ opens the next in the same episode, the probation extended through its duration; a third incomplete CQ is recorded as incomplete and the membership ends by the three-CQ rule — no fourth CQ, no manufactured vote, CARE informs the LO (HQ-2, 24 Sep 2026; MP-9 as corrected by layer 10). While a completion CQ is open no Coordinator substitutes an acceptance or an ending; a settings change never alters a recorded route. Rights: a `PROBATIONARY` membership counts for K-2's population, holds no `COORDINATOR` office (the counting rule: an effective Coordinator's membership folds `ACTIVE`), and is excluded from CQ populations (PF-07; S-6); reading discussions is DISCUSSION's audience rule over the state token. **The old flag mapped:** `Probationary_Learner` + `Probation_end` (`2_ORGANISM_TABLES.py` lines 486–487) → the `PROBATIONARY` token with the commencement content; `Probationary_Learner_role`, `Probation_learner_label`, `Days_for_probation`, the discussion-openness settings (lines 164–167 and the LO/Circle settings) → PE-08 settings; "Probationary Learner cannot be CC" (line 520) → the counting rule. Existing probations complete after lapse — the completion CQ and its permitted retries are dependent steps of the commencement, not new undertakings (MP-7 as corrected, AV-1); an enablement option hidden after lapse hides no existing status. **The old flag mapped onto the membership period, explicitly:** `Probationary_Learner = True` ↔ the period's current state is `PROBATIONARY`; `Probation_end` ↔ the commencement transition's `effective_until` under `AUTOMATIC`, and its content's `qualification_deadline_at` under `CQ` — the recorded deadline at which qualification opens, the operative end being the episode's close (the resolving completion row's `result_at`; layer 14); `probation_end()`'s automatic clear ↔ the `AUTOMATIC` route's timed `PROBATIONARY → ACTIVE`; its welcome Circular Discussion ↔ CARE's `PROBATION_COMPLETED` notice; its active-subscription gate and its `Probation_end > today()` comparison are not carried — the gate conflicts with completion after lapse, and the comparison would clear probation before its end (layer 10 `02_DISPOSITIONS` §6).

⟦editorial⟧ MP-9 — the probation completion route (layer 9), replaced in full by layer 10 RT10-20 (HQ-2, AV-1, §6) with its alternatives, recommendation and evidence lines replaced by RT10-21:

---

**⟨C.037⟩** · trace: L01 LD_RETURN_BLOCK_C_UNIT_2_ACTOR_IDENTITY_CAPACITY_ENTITLEMENT_FORMS_v1_0.md line 86
· words replaced from L09 02_REPLACEMENTS_AND_ADDITIONS.md line 209
· status: **DECIDED — R1-D14 (PROBATIONARY counts for K-2's population; rights separately governed); B-G4 computes under the lock — PROPOSED**
· supersedes: L01 LD_RETURN_BLOCK_C_UNIT_2_ACTOR_IDENTITY_CAPACITY_ENTITLEMENT_FORMS_v1_0.md line 86

- **Counting** (K-2): *effective Coordinators* = assignments whose fold at `(now, now)` is `ACTIVE` against an active `COORDINATOR` definition, held by a membership whose fold is `ACTIVE`; *current memberships* = memberships whose fold is `ACTIVE` or `PROBATIONARY` (decided, R1-D14: "An admitted membership in PROBATIONARY counts as a current membership for K-2's population assessment, including whether the LO has zero remaining members. This does not itself confer Coordinator authority, voting entitlement or other participation rights. Undecided applications, unaccepted invitations and merely proposed memberships do not count."). **Never a count of anchor rows** `[H — K-2]`. The coverage guardian (B-G4) computes both under the serialization lock.

---

**⟨C.038⟩** · trace: L01 LD_RETURN_BLOCK_C_UNIT_2_ACTOR_IDENTITY_CAPACITY_ENTITLEMENT_FORMS_v1_0.md line 87
· words replaced from L02 02_BLOCK_C_REVISED_SECTIONS.md line 132
· status: **PROPOSED — layer 1 (PC-11 Circle membership; B-G5) as re-worded by layer 2 C-4 (a); DECIDED beside it — CD-6 (a): AMEND-A NOT REQUIRED; the chain-free A-11 is carried (DQ-2, resolved within function) and PC-11 is the fall-back**
· supersedes: L01 LD_RETURN_BLOCK_C_UNIT_2_ACTOR_IDENTITY_CAPACITY_ENTITLEMENT_FORMS_v1_0.md line 87
· note ⟦editorial⟧: 'which is before the Human Governor' in the layer-2 sentence is HISTORY: CD-6 decided 24 Sep 2026 (RT-18).

- **Circle membership requires parent-LO membership — S-2's representation, which PKT-B owes.** The landed schema has **no Circle-membership relation at all** `[R — models.py: Circle has parent_organism and no member link; contextual role assignment has a circle FK]` — Circle participation today is expressible only as a role assignment scoped `CIRCLE`. **PC-11, for adoption:** `core_circlemembership(id, circle_id FK, membership_id FK core_organismmembership, …)` as a **a chain that would take the §5.1 contract and the §5.2 fold; this requires AMEND-A/B/C on §5.4 (Block B §1), which is before the Human Governor; the chain-free alternative is A-11/A-12/A-15 with its own chain** (anchor + head + append-only transitions + the §5.1 contract), states `PROPOSED / ACTIVE / ENDED` (a subset of the membership vocabulary; a ruling closes it), **guardian B-G5: `AFTER INSERT` deferred and standing — a Circle membership's LO membership must fold `ACTIVE` at the Circle membership's effective time, and an LO departure cascades `→ ENDED` on every Circle membership it holds (Ruling 4, derived not decided)**. **Alternative, not taken:** Circle participation as a `CIRCLE`-scoped role assignment only — fails K-2 ("Circle membership requires parent-LO membership" presupposes a membership, not an office) and C-1 (an APPLICATION Circle *member* need not hold any office). **Cost:** a thirteenth chain, thirteen-table canonical coverage, one more anchor in `s015_anchor_map()` — a form change already inside the single bump (§10). **Falsifier:** a ruling that Circle membership is an office — none.

⟦editorial⟧ A-11 — Circle participation without a chain (layer 2 C-4 (a)), the carried design under CD-6 (a):

---

**⟨C.039⟩** · trace: L02 02_BLOCK_C_REVISED_SECTIONS.md line 134
· status: **PROPOSED — layer 2 (A-11); carried under CD-6 (a) (DQ-2); its 'honest limit' sentence is answered by CD-6 (a) — HISTORY as a limit; a suspended Circle participant would need a chain — no ruling requires one (CD-1: out of scope)**

**A-11 — Circle participation without a chain (proposed).** Two append-only relations with the four-time grammar: `core_circleparticipation(participation_id, circle_id, membership_id, actor, enterer, decision_reference, four-time columns)` for admission, and `core_circleparticipationend(participation_id UNIQUE, actor, enterer, rule_reference, four-time columns)` for ending. *Participant of Circle C at (`state_at`, `known_at`)* = an admission row eligible at those coordinates with no ending row eligible at them — a set-difference read on the same eligibility predicate the reducer uses, with no head, no state token, no transition function and no §5.2 fold. The K-2 cascade (LO membership `ENDED` ends Circle participation) is a composed ending row per participation under a PC-14 rule (Rulings 4 and 7). Route R4's qualifying fact is an eligible participation row. **Cost:** no intermediate participation states (a suspended participant would need a chain). **Honest limit:** if the Human Governor reads §5.4's intent as covering *any* relation read at an effective time, A-11 does not escape it and AMEND-A is the only route; A-11 escapes §5.1's *contract* (head, transition function, vocabulary, guardian), which is what the clause words.

---

**⟨C.040⟩** · trace: L02 02_BLOCK_C_REVISED_SECTIONS.md lines 140–142
· status: **PROPOSED — layer 2 (PC-06 and PC-14 as relations outside §5.4 — confirmed by CD-6 (a)); status roll-up (layer 2) — see layer 3 R-C8 for the four-state vocabulary**

**PC-06 and PC-14 — status as relations outside §5.4 (unchanged in substance, now stated).** `core_systemprincipal` (PC-06): append-only principals; no head, no state, no fold, no §5.1 guardian; carries no state §5.4 allocates to an aggregate. `core_governedrule` (PC-14): append-only versioned rule texts; no head, no organism-state fold; B-G3 reads it; it is not a chain of the twelve. Both sit outside §5.4 without amendment. Both remain proposals.

**Status (AC-6).** AMEND-A/B/C: proposed amendments requiring Human authority. A-11, A-12, A-15, PC-06, PC-14, B-G2, B-G3, B-G5: proposed. Nothing installed.

## ⟦editorial⟧ C.4 §4 — The Learner-capacity contract (U-3) and genesis without circular prerequisites

