# PKT-B SLICE 2 — DESIGN EXTRACT — for the Copilot Making Engine

Extracted verbatim by US015-B-8 (Vision Chamber), 29 September 2026, from the implementation baseline's operative reading: `CANDIDATE_v1_27_OPERATIVE_READING.md`, 1,282,633 B, SHA-256 `07d629f7344d165f0962639a447a171cb44b3a7ade237e1b188a21fff356ffef` (inside candidate v1.27, `839cfb4e…8a1d`), relayed by the Human Governor for this purpose. Each paragraph is reproduced whole, from its opening line to the line before the next paragraph, with its trace and status lines. **Status words matter:** DECIDED is binding; PROPOSED is the design as it stands; OPEN is an undecided Human question — build nothing that decides it; HISTORY is not operative. Where this extract and the reading differ, the reading governs.

S1's extract (`PKT_B_S1_DESIGN_EXTRACT_v1_0.md`, `b381fe99…5c7f`, committed at `aa00ca4`) also applies — in particular ⟨B.049⟩ and Unit 2 §3 (⟨C.037⟩–⟨C.040⟩: membership and office are separate facts).

Paragraphs, and why each is here:
- **⟨B.050⟩** (reading lines 570–575) — contextual-role edges — the S2 edge set
- **⟨C.064⟩** (reading lines 1580–1585) — appointment terms and the tolling setting, snapshotted on the appointment
- **⟨E2.130⟩** (reading lines 4996–5001) — LD-Q5 — no tolling by default
- **⟨E1.038⟩** (reading lines 3165–3170) — role suspension — settings, capability, constitutional basis
- **⟨E1.037⟩** (reading lines 3153–3163) — K-1 settings as constitutional state (context)
- **⟨E1.039⟩** (reading lines 3172–3183) — HD-E6 OPEN — how a settings change is recorded (context; blocks settings writes)
- **⟨C.051⟩** (reading lines 1452–1465) — U-3 — severance, deactivation, departure, loss of office: four distinct acts
- **⟨C.023⟩** (reading lines 1042–1055) — the multi-row commands — row 7: departure ending dependent offices, composed in the same transaction
- **⟨C.056⟩** (reading lines 1494–1506) — the rule home (PC-14) — context for composed rows; HD-C5 OPEN
- **⟨E1.016⟩** (reading lines 2946–2957) — K-3 rows 1–3
- **⟨E1.017⟩** (reading lines 2959–2983) — K-3 rows 4 and 4′ — rejoining, then a fresh appointment
- **⟨E1.018⟩** (reading lines 2985–2988) — K-3 row 5 — invitation without automatic office
- **⟨E1.019⟩** (reading lines 2990–2993) — founder protection during birth
- **⟨U5.010⟩** (reading lines 5675–5687) — REM — the removal-authority interface

Final authority remains with Carmian Owen, Human Governor.

---

**⟨B.050⟩** · trace: L01 LD_RETURN_BLOCK_B_CLAUSE_MAP_AND_PROPOSED_SPECIFICATION_v1_0.md line 229
· words replaced from L09 02_REPLACEMENTS_AND_ADDITIONS.md line 299
· status: **DECIDED — CD-1 (role suspension: subscriber capability on a constitutional basis; no tolling by default; AT-02/AT-03); K-3; U21g-6**
· supersedes: L01 LD_RETURN_BLOCK_B_CLAUSE_MAP_AND_PROPOSED_SPECIFICATION_v1_0.md line 229

| **contextual-role assignment** | `PROPOSED → ACTIVE` (appointment, an explicit act — K-3; the governing term and the tolling setting in force snapshotted on the appointment, AT-02) · `ACTIVE → ENDED` (END; REASSIGN leaves `ENDED` `U21g-6`; loss of office on departure/deactivation U-3; instrument ending Ruling 4; the recorded term's end under H, tolled only by a lawful setting) · `ACTIVE ⇄ SUSPENDED` (**role suspension — a subscriber capability exercised only on an established constitutional basis by an authorised decision; CARE assists and creates no authority; an absent basis needs a valid amendment first; suspension never implies membership or Circle-participation suspension — CD-1, AT-03**) · `PROBATIONARY → ACTIVE` | `ENDED` terminal | a fresh appointment is a new assignment anchor (no uniqueness constraint blocks it `[R]`) | PKT-B | founder protection during birthing as a refusal on `ACTIVE → ENDED` by a later-appointed Coordinator (E1 item 3); `PRIVACY_TRANSFORMED` (RQ-2) |

---

**⟨C.064⟩** · trace: L09 02_REPLACEMENTS_AND_ADDITIONS.md line 309
· status: **DECIDED — CD-1 (PC-15 §(f): appointment terms and the tolling setting snapshotted on the appointment; no tolling by default); LIMIT — configurable tolling unmodelled (S-4, EL-2)**

**PC-15 §(f) — appointment terms and the tolling setting (CD-1).** An appointment's governing term (`effective_until`, `effective_until_state`) and the value of the LO setting `suspension_tolls_term` in force at the appointment's `effective_at` are the appointment's own content — snapshotted on the `PROPOSED → ACTIVE` assignment event exactly as H-4's windows are snapshotted on `SEQUENCE_START`. A later settings change or entitlement change never alters an existing term; subscription expiry invents no reinstatement, removal or extension. The default is no tolling: the term ends when the recorded term says, whatever suspensions intervened; under a snapshot of `Yes`, a suspension interval is excluded from the term's running (the guardian's floor reads the snapshotted rule). S-4: this configurable semantics is not established by the witness and is unmodelled here.

⟦editorial⟧ A-15 — Entitlement as interval facts, not a chain (layer 2 C-4 (a)), the carried design under CD-6 (a):

---

**⟨E2.130⟩** · trace: L01 LD_RETURN_BLOCK_E2_UNIT_4_MEMBERSHIP_ROUTES_RECOVERY_DISSOLUTION_v1_0.md line 91
· replaced in full by L09 02_REPLACEMENTS_AND_ADDITIONS.md line 284
· status: **DECIDED — CD-1 (LD-Q5: K-2's windows never toll; an office's fixed term does not toll by default; a lawful subscriber setting may provide otherwise — AT-02, AT-03)**
· supersedes: L01 LD_RETURN_BLOCK_E2_UNIT_4_MEMBERSHIP_ROUTES_RECOVERY_DISSOLUTION_v1_0.md line 91

| `LD-Q5` | decided (CD-1): K-2's windows never toll (H-4); an office's fixed term does not toll by default; a lawful subscriber setting `suspension_tolls_term`, snapshotted on the appointment, may provide otherwise (AT-02, AT-03) | — |

---

**⟨E1.038⟩** · trace: L09 02_REPLACEMENTS_AND_ADDITIONS.md line 312
· status: **DECIDED — CD-1 (PE-08 additions: role_suspension_enabled, suspension_tolls_term; PC-15 capability ROLE_SUSPENSION; a suspension needs a constitutional basis and an authorised decision; CARE creates no authority)**

**PE-08 additions (CD-1).** Settings: `role_suspension_enabled` (subscriber; default No) and `suspension_tolls_term` (subscriber; default No). PC-15's capability set gains `ROLE_SUSPENSION`. Enabling the feature is not constitutional authority: a suspension is admitted only where the LO's constitutional instruments (the LIBRARY-adopted governing instruments, E1 §2) establish the power and an authorised decision through the LO's route (CQ where required) exercises it — the assignment's `SUSPEND` transition names the instrument version and the decision record as its authority; absent an instrument establishing it, a valid amendment must be adopted first. CARE identifies and explains the basis (or its absence) and creates no authority. Removal and suspension are not interchangeable powers.

⟦editorial⟧ HD-E6 — the non-state-changing LO settings action: layer 2 E1-5's technical proposal (options (a) and (b)); layer 3 §6 settled that the landed 0021 guardian admits a same-state edge (EG-4); MP-5 (layer 9) needs option (a):

---

**⟨E1.037⟩** · trace: L01 LD_RETURN_BLOCK_E1_UNIT_3_FORMATION_BIRTHING_CIRCLES_v1_0.md line 86
· words replaced from L02 03_BLOCK_E1_REVISED_SECTIONS.md lines 46–50
· status: **PROPOSED — layer 1 (PE-08 the settings as constitutional state; the settings PKT-B carries) with the audience rule as replaced by layer 2 E1-3 (rules 1–5: frozen bitemporally; the audience set follows current membership; settings transitions prospective at write; worked example; representation HD-E6); DECIDED behind it — K-1, C-1, H-2, H-4; the sole-participant default is PF-02 (E2 §1); 'SD-4 … HD-E6' — OPEN (HD-E6: option (a) needed by MP-5); CD-2 governs (AT-04)**
· supersedes: L01 LD_RETURN_BLOCK_E1_UNIT_3_FORMATION_BIRTHING_CIRCLES_v1_0.md line 86
· note ⟦editorial⟧: Layer 2's five numbered rules (lines 46–50) stand in the place of the two superseded sentences; they are inserted mid-paragraph as the source directs, so the paragraph reads across five embedded lines.

**PE-08, for adoption.** Every K-1 / C-1 / relay 4 setting is a **settings transition on the LO chain** (a `TRANSITION_LIVING_ORGANISM/SETTING` event — a non-state-changing action; **whether LO events admit a non-state-changing action is SD-4 of Unit 0 and is answered here: yes, as a proposal — HD-E6**), carrying `setting_code`, `previous_value` (record-time, like `prior_state`), `new_value`, the authority (CQ result or Coordinator route), and `effective_at`. 1. **The audience rule of a record is frozen bitemporally.** For a record X, the rule is the settings in force at (`state_at` = X's `effective_at`, `known_at` = X's `recorded_at`). A settings transition recorded after X's `recorded_at` — whatever `effective_at` it carries — is not eligible at X's `known_at` and cannot change X's rule. **Established audiences are preserved** (H-2: changes are prospective); the rule is frozen, not the people.
2. **The audience set follows current membership.** Who satisfies the frozen rule is evaluated against membership and Circle participation **at read time** (H-2: an audience rule is not a frozen list); a person who joins later and satisfies the rule is in the audience; one who left is not.
3. **Settings transitions are prospective at write.** The settings guardian limb (proposed) refuses a settings transition whose `effective_at` precedes its `received_at` unless it carries a `basis_reference` to the LO decision actually made at that earlier time (a late recording of an earlier decision). Even then, rule 1 protects every record already recorded. A backdated setting without a basis is refused, not admitted with a warning.
4. **Worked example.** Setting S1 = *Yes* from day 0. Record X recorded on day 10, `effective_at` day 10. On day 20 a transition S2 = *No* is recorded with `effective_at` day 5 and a basis. X's rule = settings at (10, known 10) = S1: X remains visible under S1 to the current audience. Record Y recorded on day 25 with `effective_at` day 8 (a late recording): its rule = settings at (8, known 25) = S2; Y was never established under S1 and is under S2. A record Z recorded on day 30, `effective_at` day 30: S2.
5. **Representation.** "The settings in force at (`state_at`, `known_at`)" is read from whichever representation HD-E6 selects (§E1-5): a same-state event on the LO chain, or a versioned support relation. The rule above is the same under both. The settings PKT-B carries: `circle_transparency ∈ {Yes, Optional}` (LO; Circles inherit; a Circle's `open_to_other_OLs` under Optional), `cq_active`, `application_circle_enabled`, `application_sole_participant_delegation` (C-1's new setting — **default returned at E2 item 1**), `recovery_windows (W1, W2)` (H-4, by entitlement), `free_circle_selection`, `circle_population_targets (min 6 / max 12 / suggested 3 — inherited from the platform's globals or the LO's own)` `[H — C-1]`. **GOVERNANCE seals are free; other types seal by entitlement; lapse never exposes a seal** — PKT-B records the setting and the entitlement; **sealing and read enforcement are DISCUSSION's** `[H]`.

---

**⟨E1.039⟩** · trace: L02 03_BLOCK_E1_REVISED_SECTIONS.md lines 85–93
· status: **OPEN — HD-E6 (raised Return 1; technical options in the correction return; latest point: when the candidate's mechanisms are decided): option (a) a same-state event with action SETTINGS_CHANGE / PHASE_ADVANCE (needs the closed action vocabulary amended — Human authority; EG-4 settled: no 0021 guardian change needed, layer 3 §6), option (b) a versioned support relation; the seat's assessment [A] is adopted by nothing; MP-5 depends on (a)**

**The problem.** An LO settings change (transparency default, sole-participant delegation, recovery windows under H-4, sealing settings) is a governed act of the LO — it needs actor, enterer, the four-time grammar and Ruling 9's capacity test — but it changes no LO state: `ACTIVE` stays `ACTIVE`. PE-08's audience rule and PF-09's `settings_basis` both read "the settings in force at (`state_at`, `known_at`)".

**Option (a) — a same-state event on the LO chain.** A transition on the landed LO chain with `state = prior_state` and an action token `SETTINGS_CHANGE`, carrying a reference to the settings content (a settings document row). The §5.2 fold is untouched (same state); the settings in force at (`state_at`, `known_at`) = the content named by the last eligible `SETTINGS_CHANGE` event. **Requires:** an amendment of the closed action vocabulary (v0.13 §4.1 / §5.5 — Human authority); and confirmation that the landed guardian admits a same-state transition (whether `0021` refuses a no-op edge is not known to this seat — **EG-4**; if it refuses, (a) needs a guardian change as well). **Benefit:** settings changes are on the LO's own chain with the LO as actor and no new relation. **Cost:** every settings change becomes a chain event and the vocabulary amendment.

**Option (b) — a versioned support relation.** `core_organismsettingsversion(organism_id, version_ordinal, settings_document, actor, enterer, four-time columns)`, append-only, `UNIQUE (organism_id, version_ordinal)`; the settings in force at (`state_at`, `known_at`) = the last eligible version by the four-time order. No head, no state token, no §5.1 guardian; the same honest limit as A-11 (§C-4): it is a last-writer read over a document, and whether §5.4's intent reaches it is the Human Governor's reading. **Benefit:** no vocabulary amendment; no chain event. **Cost:** a second temporal read alongside the chain; the LO's actor/capacity test lives on this relation's guardian, not the chain's.

Under either option the settings guardian limb of §E1-3 rule 3 (prospective at write unless a basis) applies.

**Seat's technical assessment `[A]`, adopted by nothing:** (b) is the smaller change to landed objects and to v0.13; (a) is the more faithful to "one chain per Organism" and should be chosen if the Human Governor reads §5.4's intent as covering (b). The choice is HD-E6's and interacts with AD-06. **EG-4** is recorded either way.

---

**⟨C.051⟩** · trace: L01 LD_RETURN_BLOCK_C_UNIT_2_ACTOR_IDENTITY_CAPACITY_ENTITLEMENT_FORMS_v1_0.md lines 99–107
· status: **DECIDED — U-3 (the four acts); layer 1 table; 'root-portal membership: see E2 item 5' — DECIDED R1-D16 (no automatic root membership); the deactivation row's consequences are composed rows under a PC-14 rule (PROPOSED)**

## 6. SEVERANCE, DEACTIVATION, DEPARTURE, LOSS OF OFFICE — FOUR DISTINCT ACTS; NO REBINDING; REACTIVATION

| act | chain | actor | what it does | what it does not do |
|---|---|---|---|---|
| **identity severance** (takedown) | **no chain event** — the resolution row is deleted and the severance ledger written `[R — 0022 s015_0022_resolution_severance → core_identityresolutionsevered]`; the takedown's own governed record belongs to the privacy workflow (U-12, not PKT-B) | the authorised privacy workflow | the referent resolves to nothing; **rebinding is refused for ever** `[R — s015_0022_resolution_reuse; H — R-d]` | ends no membership, no office; deactivates no account `[H — U-3]` |
| **account deactivation** (voluntary) | `DEACTIVATED` on the Learner-origin chain (PC-12); **and, as linked composed consequences with `composing_rule_reference` = the U-3 rule and `enterer` = the account service**, `→ ENDED` on every current membership, every Circle membership (cascade), every active assignment | the Learner (the account act); the consequences carry actor `NONE` + rule, **not** a fabricated Organism decision `[H — U-3]` | ends all memberships and offices | severs nothing; erases nothing |
| **departure** (from one Organism) | `→ ENDED` on that membership; cascade to its Circle memberships and assignments (Ruling 4) | the Learner | one Organism only | — |
| **loss of office** | `→ ENDED` on the assignment (END, REASSIGN, instrument ending) | the appointing authority (Organism/Circle via PC-05) or the rule | one office | ends no membership |
| **reactivation** | `REACTIVATED` on the origin chain | the Learner | restores access and continues the lineage | **restores no membership, office or assignment** `[H — U-3]`; subsequent joining uses PC-08's routes; **root-portal membership: see E2 item 5** |

⟦editorial⟧ The §6 table's added row (layer 2 C-5 (b)) as replaced by layer 3 R-C5:

---

**⟨C.023⟩** · trace: L04 01_O1_R3_PREDICATES_AND_ATOMIC_COMMANDS.md lines 114–124
· status: **PROPOSED — layer 4 ((d) rows 4–8; the cascade selects non-terminal memberships only — SM-O1-3's reading of Block E2 §3 and §5)**

| 4 | **PF-05 dissolution cascade** — Return 1 Block E2 §3 (line 63): "every membership and assignment `→ ENDED` (composed, same rule)", in the dissolution's transaction; correction E2-2 (the Circle write target) | one `ENDED` per **non-terminal** membership of the LO, each naming that membership's current period; one transition per membership | continuity for each (pred not `ENDED`, same period); non-revival ✓ because each membership's own earlier rows hold no `ENDED` | B4 |
| 5 | **Voluntary account deactivation** — Return 1 Block E2 §5 (line 71): "for every current membership, a composed `→ ENDED` (actor `NONE`, rule = U-3's departure consequence …) with cascades to Circle memberships and assignments", one transaction | one `ENDED` per current membership across every LO; one transition per membership | as #4 | B5 |
| 6 | **Zero-member dissolution** — Return 1 Block E2 §2.3 (line 43) and Block E1 §3 row 3: "the last membership `→ ENDED` fires B-G4's population limb → `LAST_MEMBER_DEPARTS → DISSOLVED` composed in the same transaction" | the departing member's `ENDED` — the only membership transition; the `DISSOLVED` row is on the LO chain | continuity | B6 |
| 7 | **Departure ending dependent offices** — Block E1 §3 row 1; Block E2 §2.2 (line 36): membership `→ ENDED` with the assignment's `→ ENDED` composed in the same transaction | the one `ENDED`; the assignment row is on another chain | continuity | (as B6) |
| 8 | **R3 period bookkeeping on every admission route** — correction `05` §5; correction E2-3 | the period row and the `PROPOSED` that names it (order (b)) | genesis or opening | W1.3, W2.7 |

**No existing command splits a required atomic operation to satisfy a predicate, and none needs to:** every row set above is admitted
as written. The cascade (#4) and the deactivation (#5) must select **non-terminal memberships only** — an `ENDED` written for a
membership already `ENDED` is refused by non-revival (probe B4b), which is the correct refusal; PF-05's phrase "every membership"
therefore reads "every non-terminal membership", parallel to correction E2-2's "one composed `CLOSED` row per non-terminal Circle"
(consequential change, named in the supersession map as SM-O1-3). The `PROPOSED → ENDED` edge that #4 and #5 need for a membership

---

**⟨C.056⟩** · trace: L01 LD_RETURN_BLOCK_C_UNIT_2_ACTOR_IDENTITY_CAPACITY_ENTITLEMENT_FORMS_v1_0.md lines 111–120
· status: **PROPOSED — layer 1 (PC-14 core_governedrule; B-G3; the platform rules to ship); LD-Q6 DECIDED (PKT-B owns the rule home); HD-C5 OPEN (before the rule home is implemented); PC-14 is a support relation outside §5.4 (CD-6 (a)); B-G3 bypass answered conditionally on H-9 — OWED**

## 7. THE RULE HOME (LD-Q6) AND THE TEMPORAL-BASIS OBLIGATION (`U21g-19`, §11.1 item 2) — DESIGNED TOGETHER — FOR ADOPTION

**The four questions LD-Q6 sets:**

1. **How a record's rule reference resolves.** **PC-14 — `core_governedrule`** (append-only, versioned): `(id, rule_uuid, code, version, scope_kind ∈ {PLATFORM, LIVING_ORGANISM, CIRCLE}, scope_id NULL, authority_reference (the ruling, setting change or constitutional instrument that made it), effective_from, effective_until NULL, condition_grammar_reference, text_commitment NULL (the `FIXED` limb, U-13), superseded_by NULL)`. `composing_rule_reference` `[R — 0022, varchar(255)]` becomes a canonical `rule:<rule_uuid>@<version>` string, and **`temporal_basis_reference`** `[R — 0021]` resolves through the same table (`basis:<rule_uuid>@<version>`) — one referent for both obligations, which is why they are designed together.
2. **Authority, effective period, applicability.** A rule applies to a record iff `scope` covers the record's anchor (platform, or the record's LO, or its Circle), and the record's **own** `effective_at` lies in `[effective_from, effective_until)` — **evaluated at the record's coordinates, never the caller's** `[H — LD-Q6, Ruling 7]`; `authority_reference` must name a governed act (a ruling capture, a settings transition on the LO chain, or a constitutional snapshot).
3. **Where the checks are enforced.** **B-G3** (`AFTER INSERT`, deferred, twelve tables + the new chains): for `actor_state = NONE`, the rule resolves, applies, and its declared condition — a **small closed grammar over the record's own subject and chain** (`window_elapsed(sequence_start, W)`, `roster_zero_occupants(role)`, `member_count_zero()`, `term_end(conferral)`) — is derivable and true; **undeclared or underivable fails closed**. The basis check: `temporal_basis_reference` resolves and the basis rule permits retrospection/prospection for the record's kind.
4. **Bypass prevention.** The guardian is a constraint trigger; **it is bypassable only by a superuser** (§12) — so LD-Q6's fourth question is answered *conditionally on H-9's role*, and the specification says so.

**The rules PKT-B must ship in `core_governedrule` (platform scope, authority = the rulings):** K-2 zero-member dissolution; H-4 W1 → Deactivated; H-4 W2 → dissolution; C-1 sole-Coordinator route; C-3 lapse transition; K-4 Part 2 rule-driven ending derives conferral endings (Ruling 4); **Ruling 7 limb three** — *the rule itself must be in effect at the record's coordinates* — is `effective_from/until` on the row; **extensibility for DISCUSSION:** `scope_kind = CIRCLE` and a `condition_grammar_reference` DISCUSSION can extend with its own grammar version. **U-13 (`FIXED`)** is `text_commitment` on this table — so the ruled third value has a home without the L2 grammar (§13). **Alternative, not taken:** rules as constitutional-snapshot content only (L2) — unreadable by a guardian until U-14. **HD-C5:** whether platform rules' `authority_reference` is a ruling capture in the IDOP registry or a platform-terms object accepted at founding (VC's H-4 recommendation).

---

**⟨E1.016⟩** · trace: L01 LD_RETURN_BLOCK_E1_UNIT_3_FORMATION_BIRTHING_CIRCLES_v1_0.md lines 38–44
· status: **PROPOSED — layer 1 (K-3 rows 1–3 as paper derivations — K-3, K-2 DECIDED); row 3 [X] executed at Block A**

## 3. K-3's FIVE DEMONSTRATIONS — each as the records written and what the fold reads

| # | scenario | records (chain: actor) | what the fold reads afterwards | rule that made it |
|---|---|---|---|---|
| 1 | **founder departs, another Coordinator remains** | membership `ACTIVE → ENDED` (founder: Learner) · assignment `ACTIVE → ENDED` (cascade, actor `NONE`, rule = departure ends dependent offices, Ruling 4) · the founder's protection lapses (a fact derived from the assignment's end) | LO continues in formation; the other Coordinator holds every power; **the founder's historical foundership is the sequence-1 event, untouched** | K-3 |
| 2 | **founder departs, no Coordinator remains** | as 1, then the coverage guardian (B-G4) composes `UNSATISFIED` → the exact restricted-continuity event → `SEQUENCE_START` with the H-4 snapshot `[X — Block A §3.4]` | Without Coordinator; the remaining members may complete formation of the **same** LO once a Coordinator is restored (K-2's recovery routes, E2) | K-3, K-2 |
| 3 | **final member departs** | membership `→ ENDED` · **`LAST_MEMBER_DEPARTS → DISSOLVED`** composed at once (actor `NONE`, rule = zero-member dissolution) `[X]` | dissolved, permanently; lineage preserved | K-2 |

⟦editorial⟧ Rows 4 and 4′ are replaced by layer 2 05 §4 (walked under each representation), whose R3 column is superseded by layer 4 01_O1 (h)/(i) — see Block C §2A; under CD-3 (R3) the R1/R2 walks are HISTORY. Layer 2's §4 text:

---

**⟨E1.017⟩** · trace: L02 05_R1_R2_R3_SPECIFICATIONS.md lines 167–187
· status: **PROPOSED — layer 2 (§4 K-3 rows 4/4′ completed under each representation — AD-07); the R3 column's cells are superseded by layer 4 01_O1 §(i) (Block C §2A); R1/R2 columns HISTORY (CD-3); PE-04's founder-protection limb restated as a property of the original founding assignment row**
· supersedes: L01 LD_RETURN_BLOCK_E1_UNIT_3_FORMATION_BIRTHING_CIRCLES_v1_0.md lines 45–46

## 4. K-3 ROWS 4 AND 4′ COMPLETED UNDER EACH REPRESENTATION — **AD-07**

Paper derivations, not executions. Prerequisites for all three: the LO has not dissolved; the Learner's period 1 is closed by its `ENDED` row; an ordinary admission route (PC-08, proposed) has accepted and produced its `core_applicationdecision`. Under R1 and R3 the rows below are the ones Return 1's E1 §3 rows 4 / 4′ lacked; under R2 they are supplied for the comparison.

| step | representation R1 | representation R2 | representation R3 |
|---|---|---|---|
| open period 2 | — (no row) | insert anchor (`I`, `O`, 2) | insert `core_membershipperiod` (`membership`, 2) |
| membership `PROPOSED` | on the anchor's chain, `period_ordinal = 2`, `prior_state = ENDED` (record-time), actor the deciding party (LO or Circle), enterer per route, referencing the decision | sequence-1 on anchor 2, `prior_state` NULL, same actor/enterer | on the anchor's chain, `period_id` = period 2, `prior_state = ENDED`, same actor/enterer |
| guardian walk | opening limb: predecessor `ENDED`, state `PROPOSED`, ordinal 1 + 1 = 2 → **admitted** | chain limb not engaged (new anchor); anchor-opening limb: anchor 1 folds `ENDED` at the new sequence-1 `effective_at`, no anchor ≥ 2 → **admitted** | open-period limb: period 1 is closed (its `ENDED` names it) → not open; opening limb: predecessor `ENDED`, `PROPOSED`, period 2 fresh → **admitted** |
| membership `ACTIVE` | `period_ordinal = 2` | on anchor 2 | `period_id` = period 2 |
| **row 4 result** | member; fold `ACTIVE` inside period 2; period-1 assignment's own chain still `ENDED`; founder protection not reinstated because PE-04's limb reads an `ACTIVE` assignment **of the current period**, and none exists | member on anchor 2; anchor-1 assignments untouched; protection is a property of anchor 1's founding assignment and does not attach to anchor 2 | as R1, with the period referent being the row |
| **row 4 completes?** | **Yes** | **Yes** | **Yes** (AD-05 closed by 3.4) |
| 4′ new appointment | `TRANSITION_CONTEXTUAL_ROLE` `PROPOSED → ACTIVE` on a **new** assignment anchor referencing (`membership`, ordinal 2) | same, referencing anchor 2 | same, referencing period 2 |
| 4′ protection | founder protection is a property of the **original founding assignment row** (formation sequence-1, period 1 / anchor 1), never of the office; PE-04 tests that row's fold and its period against the current period → not reinstated | as R1 | as R1 |
| **row 4′ completes?** | **Yes** | **Yes** | **Yes** |

`ContextualRoleAssignment` has no unique constraint on membership plus role [R, as the Reviewer inspected it], so the new anchor is not blocked under any representation.

**Both rows depend on HD-C2** (which representation) and, under R1 and R3, on the HD-C2 ruling of the opening edge. Nothing here executes; nothing is installed.

---

---

**⟨E1.018⟩** · trace: L01 LD_RETURN_BLOCK_E1_UNIT_3_FORMATION_BIRTHING_CIRCLES_v1_0.md line 47
· status: **PROPOSED — layer 1 (K-3 row 5: invitation without automatic office — K-3 DECIDED)**

| 5 | **invitation without automatic office** | `TRANSITION_MEMBERSHIP` via `INVITATION_AUTO_ACCEPT` (a PC-08 route) — **no assignment written** | a Learner, not a Coordinator; an office requires 4′'s explicit act | K-3: "no automatic-Coordinator invitation setting is adopted" |

---

**⟨E1.019⟩** · trace: L01 LD_RETURN_BLOCK_E1_UNIT_3_FORMATION_BIRTHING_CIRCLES_v1_0.md line 49
· status: **PROPOSED — layer 1 (PE-04 the founder protection — Part G §3); restated by layer 2 E1-2 as a property of the original founding assignment row tested against the current period (R3, CD-3)**

**The founder protection, made effective (Part G §3):** **PE-04, for adoption** — a limb of the assignment-chain guardian: an `ACTIVE → ENDED` transition on the *founder's original uninterrupted `COORDINATOR` assignment* whose `actor_state = PARTY` names the LO with an executor who is a later-appointed Coordinator, **while the LO has not birthed** (no Birthing event effective at or before the transition's `effective_at`), is refused; the founder's own voluntary ending (executor = the founder) and account deactivation (rule-composed) are admitted. **Falsifier:** a lawful removal of a founder during birthing for misconduct — none ruled; if ruled, it is a route with its own authority, not this refusal's exception.

---

**⟨U5.010⟩** · trace: Lead Designer, U5; U5 refinement (M-7, M-2); ⟨B.049⟩ ("removal through the LO's removal authority"); ⟨OS.001⟩'s removal row; C-1 · status: **PROPOSED — REM: the removal-authority interface — the governing body, under an established constitutional basis, by a named decision route, writing `→ ENDED` via `REMOVAL`; the cascade into coverage by the carried U-3 cascade (M-7 — supplied: ⟨C.023⟩, ⟨C.051⟩, ⟨B.050⟩); DECIDED behind it — CD-1 (the authority pattern; removal ≠ suspension), CD-3 (R3: an ended period stays ended), K-2 (a removal that empties coverage starts the sequence by the write route), U-3 (the four distinct acts; loss of office on a membership's ending); the constitutional content of the basis is the LO's (C-1) and is not designed here; contest is PKT-C's**

### 3.9 REM — what "the LO's removal authority" is

**The authority.** Removal of an OL is **an act of the LO's governing body** — active GOVERNANCE through its authorised process, or the fallback body by ⟨U5.007⟩'s route where GOVERNANCE is not active — **under an established constitutional basis** that names the grounds and the process. The pattern is CD-1's, applied to removal as CD-1 applies it to suspension: subscription provides no authority; **where the constitution establishes no removal basis, no removal route exists** until a valid amendment through C-1's route establishes one; CARE identifies and explains the basis and does not create it. The root LO's offices are out of scope.

**The act.** `TRANSITION_MEMBERSHIP ACTIVE → ENDED` or `PROBATIONARY → ENDED` (⟨B.049⟩) with **`route = REMOVAL`**, actor = the LO, the **deciding act's reference** — GOVERNANCE's act under its authorised process, or the fallback act with its `route = GOVERNANCE_FALLBACK` record and references (⟨U5.007⟩) — the **constitutional basis reference**, the **decision reference** (the CQ result or the concurrence record) and the recorded grounds. **Removal writes `ENDED` and nothing else**: never `SUSPENDED`, never `RESTRICTED` (⟨U5.009⟩), never a "temporary" ending — the powers are not interchangeable (CD-1). The ended period stays ended; rejoining is a new period through the proper admission route (CD-3). **The subject is not part of the body deciding their own removal** (recusal, ⟨U5.007⟩); a sole LO Coordinator is therefore not their own removal authority — departure is that route (probe R2). The subject's contest is PKT-C's (contest, invalidation) — interface only.

**Cascade into coverage (M-7: the carried cascade, confirmed).** The membership's `→ ENDED` composes, **in the same transaction**, the member's Circle memberships and every assignment `→ ENDED` — the U-3 cascade v1.7 already carries for departure and account deactivation (⟨C.023⟩ row 7: "membership `→ ENDED` with the assignment's `→ ENDED` composed in the same transaction"; ⟨C.051⟩: "cascade to its Circle memberships and assignments (Ruling 4)"; ⟨B.050⟩: "loss of office on departure/deactivation U-3"), **keyed on the membership's ending whatever its route**: the composed rows carry actor `NONE` and `composing_rule_reference` = the U-3 rule, "not a fabricated Organism decision" (⟨C.051⟩). REM adds no cascade and relies on this one; v1.0's assumption that it exists as ⟨B.049⟩ implies is confirmed by the supplied text. B-G4 fires on each assignment's `→ ENDED` under the same lock (⟨E2.098⟩): a removal that leaves the LO with no effective Coordinator starts K-2's sequence in the removal's transaction, `effective_at` = the removal's, with the outreach that follows — the sequence is not refused, and CARE's existing warnings (⟨L18.002⟩'s zero-Coordinator explanation) are the guard (probe R1); a removal that ends a Circle's last Circle Coordinator starts that Circle's W1c (probe R3; ⟨U5.008⟩ case 1).

[T] ⟨B.049⟩, ⟨B.050⟩, ⟨C.023⟩, ⟨C.051⟩, CD-1, CD-3, K-2, U-3, ⟨E2.098⟩; [M] probe R1, R2, R3; no [E].

---

---

Final authority remains with Carmian Owen, Human Governor.
