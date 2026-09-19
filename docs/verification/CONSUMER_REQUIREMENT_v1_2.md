# CONSUMER-SIDE REQUIREMENT, STATED EXACTLY — v1.2

    Seat      : Vision Chamber, Claude Opus 5, 18 September 2026 (UFUND-5)
    Origin    : v0.5, Lead Designer, Claude Fable 5.1, 18 September 2026
                (sha256 995073dbf74a8722dbdb03a55d68acba29893534360e5e8e5364f903a3aafd20)
    Supersedes: v1.1, v1.0, v0.9, v0.8, v0.7 and v0.6 (none adopted) and, on adoption, v0.5.
    Authority : **THIS STATEMENT IS AUTHORITATIVE.** `s015_gate.py` and `gate_conformance.py` are
                implementation and test material written against it. Where an implementation and this
                statement differ, the statement governs and the difference is a defect in the
                implementation - never a reason to read the requirement out of the code. **v0.5 is preserved, not withdrawn**: both qualifying runs of
                18 September 2026 were decided against its §1-§3 and it remains the record of that.
    For       : **ONE authoritative decision implementation — `.github/s015_gate.py` — invoked on
                every path that reports qualification.** See C10.
    Status    : DESIGN STATEMENT, and one member of a single coordinated candidate correction (§6).
                It authorises nothing and implements nothing. Adoption is the Human Governor's;
                until adoption **v0.5 governs the consumers as landed**.

Vocabulary is v0.5's, unchanged. **R** = the run root; **D** = `R/evidence`. "Required" means: the record
must exist, the field must be a key of the record, and its value must not be JSON `null`. "Named refusal"
means the decision is *not qualifying* and its reason text contains the field name and the record name
given in the table, **verbatim**. Every refusal is recorded; the consumer never stops at the first one
within a step, except as §2 says.

---

## 0. DELTA FROM v0.5 — for A1 at the scheduled focused check

Six changes. Three approved in UFUND-4 and never issued; three new. **Nothing is removed.** Every record,
field, equality, consumer predicate and evidence set of v0.5 is carried forward, and the ten evidence sets
of §5 are preserved exactly.

| # | Change | Status | Where |
|---|---|---|---|
| **C1** | `route` and `attested_commit` required in §2 | APPROVED UFUND-4 | §2 |
| **C2** | E19 added, recorded as a **transport-consistency check only** with its limited rationale in the row | APPROVED UFUND-4 | §3 |
| **C3** | The dependency-environment fields required in §2 | APPROVED UFUND-4 | §2 |
| **C4** | **Which required controls ran** — `steps` and `cleanup` required; E20/E21/E22 require the **complete** control set, **each exactly once**, at its successful outcome and with its existing assertion/exit evidence | **NEW** | §2, §3 |
| **C5** | **Tree binding** — `tree`, `commit_tree`, `working_tree_git_tree` required; E23 binds them | **NEW** | §2, §3 |
| **C6** | The record is named the **launch attestation**, not the *launcher* attestation | **NEW** | §1, §2, §6 |
| **C7** | The required decision implementations are **two**, not three | v0.8 | §0.0, §4, §5, §6 |
| **C8** | §3.3 carries the settled corrections in full: the exact permitted target exit, the inventory-byte and registry-byte verification, the run-root-relative location and the parsing rules | **NEW in v0.9** | §3.3 |
| **C9** | The §4 local-task predicates are accounted for one by one | v0.9 | §4 |
| **C12** | **§2b's staging promise narrowed to what it stages.** Statement-only: no implementation changes | **NEW in v1.2** | §2b, §3.3 |
| **C11** | **A1's four blockers closed as field families**: the tables completed; a type stage over every consumed field; duplicate members and duplicate registry rows refused; mutation evidence required to agree with itself | **NEW in v1.1** | §2, §2b, §3.1, §3.3, §5 |
| **C10** | **One authoritative implementation.** The obligation to maintain a second decision implementation, and to compare implementations for agreement, is removed | **NEW in v1.0** | §0.0b, §4.4, §5, §6 |

### 0.0 C7 — the third decision implementation is removed (NEW in v0.8)

**Ruled by the Human Governor, 18 September 2026.** The required set of decision implementations is
**two**, not three: `bootstrap.gate` and `.github/s015_gate.py`.

**Ground.** Cross-implementation comparison exists to catch ambiguity in this statement, and two
implementations do that. The independence that matters is the one the implementation split already
ruled: **the seat that produces the records does not write the consumer that judges them.**
`bootstrap.gate` is inside the run; `s015_gate.py` is outside it and is written by a different seat.
Python-versus-PowerShell was never that independence.

**What it cost to learn.** A PowerShell consumer was written and corrected three times over four
exchanges - a case-insensitive variable collision, a pipeline-unwrapped array, and `@(command)`
double-wrapping. Every one was real and every one was a defect *in the third judge of the
instrument*, not in the repository, the contract, or the decision logic, which was already correct in
Python. Those defects existed **because** a second language was introduced. The attempts are
preserved in the package's FAILED_ATTEMPT records and are not withdrawn.

**What made it unnecessary.** The local qualification of 18 September 2026 was decided on Windows by
`s015_gate.py` itself, first time, returning `"qualifying": true`. The PowerShell consumer was never
load-bearing: it was inherited from Copilot CI task v0.6's local-run predicates and carried forward
as a requirement without being re-examined against the bound objective.

**What is retained.** Section 4's Copilot CI task predicates stay exactly as they are - they are
requirements on that task, which continues to exist and to run. What changes is that the task is no
longer required to implement this statement's DECISION in PowerShell: it invokes the Python consumer
and consumes its decision record. The shared harness keeps its PowerShell adapter, so an optional
implementation can still be exercised; it is simply no longer in the required set.

**Falsifier, recorded.** If the Copilot CI task must decide locally in PowerShell because something
in its delivery path cannot call Python, then a PowerShell decision is load-bearing after all and C7
is wrong. Nothing observed to date suggests that: Python ran on the surface in question.

### 0.0 C12 — §2b's staging promise, narrowed to what it stages (NEW in v1.2)

A1's bounded closure check of 19 September 2026 returned **PARTIAL CLOSURE**: F-A1-FC-01, F-A1-FC-03
and F-A1-FC-04 closed; **F-A1-FC-02 narrowly open on staging**.

**The residual, precisely.** v1.1's §2b said every consumed field it listed is type-refused **before any
comparison**, and its table listed the mutation-evidence fields and both site lists. In fact the
mutation-evidence types are checked at **E21** and an absent or null site list at **E14**, both of which
run after the §3 comparisons have begun. A1 demonstrated it: with `tests.exit` set to the string `"1"`
and the summary seal deliberately broken, the refusals come back as

    summary.json differs from the one the surface result sealed over          <- E10, a comparison
    ... the target exit is '1' (str), not the permitted integer 1             <- E21, not §2b

The first line proves a §3 comparison ran while a listed field was of the wrong type. **Reproduced
independently by the Vision Chamber on the same gate.**

**Consequence for using CI: none demonstrated.** Every malformed input still refuses, by a named
refusal. A1 found no new false qualification and no rejection of any tested valid positive. The defect
is that this statement claimed a staging it does not perform — a claim wider than its mechanism.

**The remedy is statement-only, and is taken here.** §2b now describes the stage it actually is: a type
stage over the **binding records**. Where a type is checked elsewhere, §2b says so and names the check.
No gate, control or evidence changes; the 49 permanent controls and both retained archives are
unaffected. **This correction is folded into the commit that was already to be made, so it costs no
additional qualification cycle.**

The alternative — moving mutation and site-list typing into §2b — was not taken. It would require the
consumer to reach into per-step records before the record-level comparisons, which inverts the order
the statement has had since v0.5, for no demonstrated gain in what gets refused.

### 0.0a C11 — A1's four blockers, closed as field families (v1.1)

A1's focused check of 19 September 2026 returned **NOT CLEARED** with four blockers, each with
reproducible counterexamples. All four are closed here, **as FIELD FAMILIES rather than as the
individual examples A1 supplied**, so each rule covers the family and not the instance.

| A1 finding | What was wrong | Closed in |
|---|---|---|
| **F-A1-FC-01** | The tables were incomplete: E20 required "the six fixed controls" and **this statement never named them anywhere**; `identity.valid` was enforced by the gate and absent from §2; E24 and E25 sat in §4 prose rather than the operative table; E14's accepted shape was unstated | §2, §3.1 |
| **F-A1-FC-02** | Wrong-type evidence qualified, because truthiness stood in for type validation: `refusals: false`, `refusals: 0` and `refusals: {}` all read as "no refusals", and `final_exit: false` compared equal to `0` | **§2b, new** |
| **F-A1-FC-03** | Duplicate JSON members resolved to the last value, and a duplicated registry inventory row to the first — each silently choosing a reading this statement never made | §2 rule (f), §3.3(b) |
| **F-A1-FC-04** | Mutation evidence could contradict itself: a `FAIL` count of `0` or `null` qualified beside a recorded failing target | §3.3 |

**What A1's return did not establish, and is not claimed here.** A1 examined this statement and the gate
against synthetic evidence. **It did not inspect the retained local or CI archives**, and no conclusion
about the field types in those archives is attributed to it. Those archives were separately assessed
against the corrected gate by the Vision Chamber; that assessment is distinct from a run of the
corrected gate on a new candidate, which follows.

**A1's non-blocking observations are recorded, not closed.** Entry-path prefix matching by string, an
accepted `mode` suffix, and three registry rebinding forms remain as A1 characterised them. They are
carried limitations of this statement and are outside C11.

### 0.0b C10 — one authoritative implementation (v1.0)

**Ruled by the Human Governor, 18 September 2026**, option (c): one authoritative qualification
decision implementation, invoked on both the local and the CI path.

**Required.** `.github/s015_gate.py` is **the** decision. **Every path that reports qualification must
invoke it and require its successful decision**, together with the surface-specific checks of §4.2 that
apply to that path. **A bootstrap exit of 0 alone must never qualify a run.**

**Removed.** The obligation to maintain a second decision implementation, and the obligation that
implementations agree. Cross-implementation divergence reporting retires with it. `bootstrap.gate` is
no longer a required decision implementation and needs no `--decide` entry.

**Preserved unchanged.** The bootstrap's existing in-run gate, its finalisation and its failure
propagation stay exactly as they are. **It is not refactored to remove duplication with §1-§3.** Its
job is to set `final_exit` and to fail safely; qualification is decided separately, over the retained
records, by the authoritative gate.

**Retained in full.** The positive, negative and malformed-evidence controls of §5, exercised against
the **actual authoritative gate** — not a reference model, not a second implementation.

**Ground.** The gate re-takes the decision from the same five records, outside the run, so a second
implementation protects nothing in the verdict: it cross-checks this statement for ambiguity. That is
real, but not worth a second maintained consumer — three PowerShell implementations of it produced
three defects, every one in the checking apparatus and none in the repository. **A1's focused
independent check remains required and does that work.**

**Escalation rule, as directed.** A concrete requirement or safety conflict is escalated with its
practical consequences. **A defect or ambiguity found in the gate is not by itself a reason to conclude
that another implementation is needed.**

### 0.1 Corrections the Human Governor made to v0.6, carried here

**v0.6's E21 was insufficient and is replaced.** It required "at least one `MUT-` step", which does not
establish completeness: a run omitting one of two required mutation controls would have satisfied it.
**E21 now requires the complete applicable mutation-control set**, derived from the bound candidate's
registry, each expected control **exactly once**, at its successful control outcome, with its existing
assertion and exit evidence. **E20 likewise requires each fixed control exactly once at PASS.**

**v0.6's E23 rationale conflated three distinct issues and is corrected.** See §3.2. They are: the
integrity of the tree across records (E23's subject); the resolvability of a commit id a surface names
(a reporting matter, §6.3); and the completeness of qualification reporting (a separate reporting
requirement, §6.3). E23 speaks only to the first.

**No interim wording revert.** v0.6 proposed reverting `s015_gate.py` to `"launcher attestation"` and
changing it back on adoption. That is withdrawn. The contract, both decision implementations and
their conformance expectations are prepared as **one coherent candidate correction** (§6), and the
current divergence is preserved and disclosed rather than churned.

### 0.2 Provenance of the new three

- **C4** delivers the Human Governor's ruling of 18 September: *the final qualifying evidence must
  identify the final candidate and clearly establish which required controls ran.* v0.5 requires
  `M.result == "PASS"` and `M.exit_status == 0` (E18) and takes the producer's verdict as the account of
  what executed. A run started with the bootstrap's `--skip-mutations` flag omits both mutation steps and
  satisfies every check in v0.5.
- **C5** arises from the two qualifying runs of 18 September naming different commit ids for the same
  tree — CI `b18b3677…`, local `5d503f83…`, both tree `12baae33…`. v0.5 binds the commit four ways (E6)
  and never binds the tree.
- **C6** corrects the last place a settled misattribution survives. The attestation is written by the
  **bootstrap**; v0.5's own §1 table says so and then names the record the *launcher* attestation in its
  verbatim refusal text. **Declared, because it is a defect of this seat:** on 18 September the Vision
  Chamber changed `s015_gate.py`'s refusal text to *"launch attestation"* unilaterally, holding this
  statement's sha256 but not its bytes. See §6.2 for the divergence that created and its exact limits.

---

## 1. Records the decision consumes (all five; a missing record is a named refusal)

| Record | Location | Written by | Named refusal when absent |
|---|---|---|---|
| `LAUNCH_ATTESTATION.json` | R | bootstrap, before the coordinator starts | `final evidence missing: no launch attestation in the run` **(C6)** |
| `POST_RUN_SNAPSHOT_CHECK.json` | R | bootstrap, after the coordinator exits | `final evidence missing: no final snapshot witness (POST_RUN_SNAPSHOT_CHECK.json) in the run` |
| `SURFACE_RESULT.json` | R | bootstrap, as its last act | `final evidence missing: no completed surface result (SURFACE_RESULT.json) in the run` |
| `summary.json` | D | coordinator (route) | `final evidence missing: no coordinator summary in the run` |
| `SOURCE_AUDIT.json` | D | coordinator, after the route returns | `final evidence missing: no source audit (SOURCE_AUDIT.json) in the run` |

If any record is absent, the decision is *not qualifying* with every absence named, and **no field or
equality check is made**.

---

## 2. Required binding fields, by record — checked FIRST, before any comparison (A1-D04-01)

Added fields marked; everything unmarked is v0.5's.

| Record | Required fields | Named refusal for an absent field `<f>` |
|---|---|---|
| `LAUNCH_ATTESTATION.json` | `run_id`, `launch_token`, `commit`, `inventory_digest`, `snapshot`, `site_prequalification`, **`tree` (C5)** | `required binding field <f> is absent from the launch attestation (LAUNCH_ATTESTATION.json): refused before any comparison` **(C6)** |
| `LAUNCH_ATTESTATION.json` → `site_prequalification` | **`validated_dependency_root`, `validated_site_packages`, `refusals` (C3)** | `required binding field <f> is absent from the launch attestation site prequalification (LAUNCH_ATTESTATION.json site_prequalification): refused before any comparison` |
| `POST_RUN_SNAPSHOT_CHECK.json` | `run_id`, `launch_token`, `commit`, `inventory_digest`, `unchanged`, `digest_after` | `required binding field <f> is absent from the final snapshot witness (POST_RUN_SNAPSHOT_CHECK.json): refused before any comparison` |
| `SURFACE_RESULT.json` | `run_id`, `launch_token`, `commit`, `inventory_digest`, `digest_after`, `post_run_snapshot_unchanged`, `post_run_witness_sha256`, `coordinator_exit`, `summary_sha256`, `source_audit_sha256`, `final_exit` | `required binding field <f> is absent from the completed surface result (SURFACE_RESULT.json): refused before any comparison` |
| `summary.json` | `run_id`, `result`, `exit_status`, `execution`, `identity`, **`route` (C1)**, **`steps` (C4)**, **`cleanup` (C4)** | `required binding field <f> is absent from the coordinator summary (summary.json): refused before any comparison` |
| `summary.json` → `execution` | `launch_token`, `root`, `entry`, `mode` | `required binding field <f> is absent from the coordinator summary execution record (summary.json execution): refused before any comparison` |
| `summary.json` → `identity` | `commit`, **`attested_commit` (C1)**, **`commit_tree`, `working_tree_git_tree` (C5)**, **`valid` (C11)** | `required binding field <f> is absent from the coordinator summary identity record (summary.json identity): refused before any comparison` |
| `summary.json` → `cleanup` | **`outcome` (C4)** | `required binding field <f> is absent from the coordinator summary cleanup record (summary.json cleanup): refused before any comparison` |
| `SOURCE_AUDIT.json` | `run_id`, `launch_token`, `refusals` | `required binding field <f> is absent from the source audit (SOURCE_AUDIT.json): refused before any comparison` |

Rules (a)–(d) are v0.5's, unchanged: every absent field named; an absent field stops the decision here so
no absent value is ever compared with anything, including another absent value; **no default and no
fallback** for any field.

**Rule (d) is REPLACED by C11.** v1.0 said a field of the wrong type "is treated by the equality checks
of §3". That left type validation to comparison, in a language where `false == 0` holds. **A field of the
wrong type is now refused by name at §2b, before any comparison.**

**Rule (f), added with C11 — repeated members.** Every record is read by a parser that **refuses a
repeated member** rather than keeping the last occurrence. `{"final_exit": 2, "final_exit": 0}` is
contradictory evidence, and a reader that silently resolves it has decided something this statement did
not. Named refusal: `final evidence contradictory: <record> contains a repeated member '<name>':
refused`. The decision stops there.

**Rule (e), added with C4 — the shape of `steps`.** `steps` must be a non-empty list whose every element
is an object carrying `id` (a non-empty string) and `outcome`. A malformed `steps` is a named refusal at
this stage and the decision stops here:
`the coordinator summary's step record is not a list of steps each carrying id and outcome`.
**Step ids must be unique within `steps`**; a repeated id is a named refusal at this stage:
`the coordinator summary records step id <id> more than once: the step record is contradictory`.
Uniqueness is checked here, before §3, so that E20 and E21 compare against an unambiguous record.

---

## 2b. The type stage — the binding records, by family (C11, narrowed by C12)

**Scope (C12): the BINDING RECORDS** — the attestation and its site prequalification, the witness, the
surface result, and the coordinator summary's own top-level, execution, identity and cleanup records.
For those fields, a wrong type is refused **after presence and before any comparison**, by name, never
coerced and never compared.

**Truthiness is not type validation**: `false == 0` holds, `{}` and `0` are falsy, and a boolean is an
integer. That is why this stage exists.

**Types checked elsewhere, and where (C12).** This stage does not reach into per-step records, and it
does not act on an absent or null site list. Those are typed at the check that consumes them, and a
wrong type there is still a named refusal — just not at this stage:

| Fields | Typed at | Refusal |
|---|---|---|
| `steps[].mutation_applied`, `steps[].tests` and its `exit`, `ran`, `by_outcome`, `not_ok`, `reconciled` | **E21**, via §3.3's per-control table | `the run's mutation controls are incomplete or unsound: <what was found>` |
| `interpreter.site` or `execution.site_dirs` **absent or null** | **E14** | `the prequalified site list or the recorded site directories are not a list of paths: refused rather than compared` |

A site list that is **present and not null** but of the wrong shape — a scalar, or an array containing a
non-string — is refused at this stage, by the template below.

| Family | Accepted JSON | Fields |
|---|---|---|
| **text** | string | every digest, identity, path and label: `run_id`, `launch_token`, `commit`, `tree`, `inventory_digest`, `digest_after`, `post_run_witness_sha256`, `summary_sha256`, `source_audit_sha256`, `snapshot`, `root`, `entry`, `mode`, `route`, `result`, `attested_commit`, `commit_tree`, `working_tree_git_tree`, `validated_dependency_root`, `validated_site_packages`, `cleanup.outcome` |
| **count** | integer, **never a boolean** | `final_exit`, `coordinator_exit`, `exit_status` |
| **flag** | `true` or `false` | `unchanged`, `post_run_snapshot_unchanged`, `identity.valid` |
| **list** | array | `site_prequalification.refusals`, `SOURCE_AUDIT.refusals`, `steps` |
| **record** | object | `site_prequalification`, `execution`, `identity`, `cleanup` |
| **list of text** | array whose every member is a string, **empty permitted** | `site_prequalification.interpreter.site`, `execution.site_dirs` — **where present and not null**; see above |

Named refusal: `binding field <f> of the <record> is a <found>, not a <expected>: refused before any
comparison`. For the two site lists: `binding field <f> of the <record> is not a JSON array of strings:
refused before any comparison`.

**The empty-list policy, stated (C11).** An empty array is a valid value for every field in the **list**
and **list of text** families. It means *no members*, and for a refusal list it means *no refusals* —
exactly what E15 and E17 require. `false`, `0` and `{}` do not mean that and are refused here.

---

## 3. Equality and completeness checks

Let A = attestation, P = witness, S = surface result, M = summary, X = M.execution, I = M.identity,
U = source audit, T = M.steps, C = M.cleanup.

### 3.1 The checks

E1–E18 are v0.5's, unchanged in requirement and in refusal text.

| # | Requirement | Named refusal |
|---|---|---|
| E1 | `S.final_exit == 0` | `the surface's final exit is <value>, not 0: the run did not complete cleanly (post-run check, coordinator or cleanup)` |
| E2 | `S.coordinator_exit == 0` | `the coordinator exited <value>` |
| E3 | `S.post_run_snapshot_unchanged is true` **and** `P.unchanged is true` | `the final snapshot witness reports a change during the run (Q6)` |
| E4 | `A.launch_token == P.launch_token == S.launch_token == X.launch_token == U.launch_token` | `launch token disagrees across attestation, final witness, surface result, summary and audit` |
| E5 | `A.run_id == P.run_id == S.run_id == M.run_id == U.run_id` | `run id disagrees across attestation, final witness, surface result, summary and audit` |
| E6 | `A.commit == P.commit == S.commit == I.commit` | `the commit disagrees between attestation, final witness, surface result and identity` |
| E7 | `A.inventory_digest == P.inventory_digest == S.inventory_digest` | `the pre-run snapshot inventory digest disagrees between attestation, final witness and surface result` |
| E8 | `A.inventory_digest == P.digest_after == S.digest_after` | `the snapshot inventory digest disagrees between attestation, surface result and final witness` |
| E9 | `sha256(bytes of POST_RUN_SNAPSHOT_CHECK.json) == S.post_run_witness_sha256` | `POST_RUN_SNAPSHOT_CHECK.json differs from the one the surface result sealed over` |
| E10 | `sha256(bytes of summary.json) == S.summary_sha256` | `summary.json differs from the one the surface result sealed over` |
| E11 | `sha256(bytes of SOURCE_AUDIT.json) == S.source_audit_sha256` | `SOURCE_AUDIT.json differs from the one the surface result sealed over` |
| E12 | `A.snapshot == X.root` | `the evidence's root is not the attested snapshot` |
| E13 | `X.entry` begins with `A.snapshot` | `the entry did not execute from the attested snapshot` |
| E14 | sorted `A.site_prequalification.interpreter.site` == sorted `X.site_dirs` | `the live site directories differ from the prequalified list` |
| E15 | `A.site_prequalification.refusals` is empty | `the attestation records startup refusals` |
| E16 | `X.mode` begins with `snapshot-entry` | `the route recorded a checkout-entry (non-qualifying) run` |
| E17 | `U.refusals` is empty | `the source audit refused (detected after execution): <refusals, joined>` |
| E18 | `M.result == "PASS"` and `M.exit_status == 0` | `result not PASS/0` |
| **E19 (C2)** | `I.attested_commit == A.commit` | `the attested commit did not survive transport to the coordinator summary` |
| **E20 (C4)** | each of the six **fixed controls** appears in `T` **exactly once**, and **every** element of `T` has `outcome == "PASS"` | `a required control is missing, duplicated or did not pass: <each missing id; each id with a count other than one and its count; each step id with its non-PASS outcome>` |
| **E21 (C4)** | the **complete applicable mutation-control set**, derived per §3.3, appears in `T` — each **exactly once**, `outcome == "PASS"`, `mutation_applied is true`, and the assertion/exit evidence of §3.3 present — and `T` contains **no** `MUT-` step outside that set | `the run's mutation controls are incomplete or unsound: <each missing expected id; each id with a count other than one; each unexpected MUT- id; each id whose control evidence is absent or contradictory, with what was found>` |
| **E22 (C4)** | `C.outcome == "CLEAN"` | `cleanup did not report CLEAN: <value>` |
| **E23 (C5)** | `A.tree == I.commit_tree == I.working_tree_git_tree` | `the candidate tree disagrees between the launch attestation and the coordinator summary identity` |
| **E24 (C9; operative from v1.1)** | `I.valid is true` | `the coordinator summary records an invalid identity: <value>` |
| **E25 (C9; operative from v1.1)** | `M.route` equals the route version this statement names | `the evidence is from <found>, not the route this decision is written against (<expected>)` |

**The required control set for E20 is exactly these six ids, enumerated here (C11):**

    IDENTITY   OFFLINE   SELF   S015   OWNERSHIP   LOCK

They are fixed, and **a consumer takes them from this list and from no other source.** Implementation
material and the route's own planning cannot supply law this statement omits. v1.0 required "the six
fixed controls" and named them nowhere at all; that is A1's F-A1-FC-01 and it is closed here.

Digests are computed over the file's exact bytes as stored (no re-serialisation, no line-ending
normalisation). E9 is a seal *in addition to* E4–E8; a consumer that checked E9 alone would not satisfy
this statement.

### 3.2 What E19 and E23 are, and are not

**E19 is a transport-consistency check and nothing more.** `attested_commit` is the same value the
surface supplied, carried to the summary along a longer path. It is **not** an independent observation
and must never be reported as corroboration of the commit. **E6 is the independence check**: it compares
the attestation, the witness, the surface result, and the identity the coordinator observed from the
checkout. E19 exists so that a value silently lost or altered in transport is named. Recorded at this
length because describing `attested_commit` as an independent source was a seat error corrected on the
Human Governor's challenge in UFUND-4.

**E23 is an integrity check on the tree across records, and only that.** It requires that the tree the
bootstrap attested, the tree of the commit the coordinator observed, and the tree of the working files it
observed are one value. It is about the internal consistency of one run's evidence. **It says nothing
about whether any commit id in that evidence is resolvable by a third party, and nothing about what a
qualification report must contain.** Those are two further and distinct matters, and they are stated in
§6.3, not here. Conflating the three was an error in v0.6, corrected on the Human Governor's direction.

### 3.3 Deriving the applicable mutation-control set (E21)

Every requirement below is settled and is stated here rather than left in an implementation or a
commission. A consumer is conformant only if it does all of it.

**(a) Location — relative to the retained run root, never through the attested path.** The registry is
`<run root>/snapshot/verification/mutations.py`. The absolute path in `A.snapshot` records where the
snapshot stood on the machine that produced the run; a consumer examining a downloaded artifact is
elsewhere, and on the producing machine that path may still exist while holding a **later run's
snapshot**. Reading through it would let a decision over one candidate's evidence be taken using
another candidate's registry. The attested path remains bound by E12; **binding a path and using it to
find a file are different jobs.** If `A.snapshot` does not end in `snapshot`, refuse.

**(b) The bytes are verified against the bound candidate before they define anything.** Locating the
file says only *where* to look. Therefore, in order:

1. read `<run root>/execution_inventory.json`; **its own sha256 must equal `A.inventory_digest`**;
2. find its row for `verification/mutations.py` — there must be **exactly one** (C11). Two rows with
   conflicting digests are contradictory evidence, and taking either silently chooses a reading this
   statement never made. Named refusal: `the mutation registry cannot be derived: the bound inventory
   carries <n> rows for verification/mutations.py: exactly one is required`;
3. the retained file's sha256 must equal that row's `sha256`.

Without step 1 an empty registry can be written into the retained snapshot and the mutation steps
dropped, and the run qualifies with commit, tree and inventory values untouched — **because those are
recorded values that nothing recomputes.**

**(c) Parsed, never imported.** The module imports Django at module level; importing it would execute
code belonging to the artefact under test inside the consumer that judges it.

**(d) The accepted format is a line grammar, and it REFUSES rather than skips.**

       line 1 of the block : MUTATIONS = {        exactly, at column 0
       key lines           :     "<key>": {       exactly four spaces, double quotes, then ": {"
       last line           : }                    exactly, at column 0
       <key> is one or more of  A-Z a-z 0-9 . _ -

   Every other line at the block's own four-space indent must be `    },` or `    }`. **Anything else
   there is a named refusal, never skipped.** Skipping is how a consumer derives a *smaller* registry,
   and a smaller registry means fewer expected controls — the completeness hole E21 exists to close.
   Lines indented deeper, and blank lines, belong to a value and are ignored.

**(e) A consumer that also has a language parser must apply both and refuse when they disagree.** The
grammar exists so that a consumer without a parser accepts exactly what one with a parser accepts. A
mixed double- and single-quoted registry is accepted by Python's `ast` and rejected by the grammar;
such a file must be refused, not read two ways.

**Named refusals when the set cannot be derived** — each ends the decision as *not qualifying*. There
is **no fallback to a shorter set, and no producer-declared set is accepted in its place.**

| Condition | Named refusal |
|---|---|
| `A.snapshot` is not the run root's snapshot directory | `the mutation registry cannot be derived: the attested snapshot is not the run root's snapshot directory` |
| the snapshot is absent from the retained run root | `the mutation registry cannot be derived: the attested snapshot is not present in the retained run root` |
| `verification/mutations.py` absent | `the mutation registry cannot be derived: verification/mutations.py is absent from the attested snapshot` |
| `execution_inventory.json` absent | `the mutation registry cannot be derived: execution_inventory.json is absent from the retained run root` |
| it does not hash to `A.inventory_digest` | `the mutation registry cannot be derived: execution_inventory.json is not the inventory the launch attestation recorded` |
| it carries no row for the registry | `the mutation registry cannot be derived: the bound inventory carries no verified row for verification/mutations.py` |
| the retained file does not match that row | `the mutation registry cannot be derived: the retained verification/mutations.py is not the bound candidate's registry` |
| a line at the block's indent is not an accepted entry | `the mutation registry cannot be derived: verification/mutations.py line <n> is not an accepted MUTATIONS entry: <line>` |
| no single top-level `MUTATIONS` assignment | `the mutation registry cannot be derived: verification/mutations.py has no single top-level MUTATIONS assignment` |
| the block declares no keys in the accepted format | `the mutation registry cannot be derived: the MUTATIONS block declares no keys in the accepted format` |
| grammar and parser disagree | `the mutation registry cannot be derived: the strict grammar and the parser disagree on the registry: grammar <a>, parser <b>` |
| a key appears more than once | `the mutation registry is ambiguous: MUTATIONS declares <key> more than once` |

**Per-control evidence required of each expected `MUT-` step** — the existing evidence, not new fields:

| Requirement | Why |
|---|---|
| `outcome == "PASS"` | the route's own disposition of the control |
| `mutation_applied is true` | the mutation actually reached the database |
| **`tests.exit` is exactly the integer `1`** | **the permitted target exit, an exact value AND an exact type.** Under the mutation the target must fail by assertion, so the test process reports one failure and exits 1. A **missing** exit, `2`, `-9`, or the string `"1"` each mean something other than a clean assertion failure and none may qualify. **A boolean is excluded explicitly**, because `True == 1` compares equal in Python and in PowerShell. `tests.exit != 0` is **not** sufficient and is superseded here |
| `tests.by_outcome` is an object whose **single key** is `FAIL` and whose value is a **POSITIVE INTEGER** | the target failed, failed only, and the record says how many times. **`{"FAIL": 0}` and `{"FAIL": null}` are refused (C11)**: a control recording zero failures beside a failing target contradicts itself |
| `tests.not_ok` is a **NON-EMPTY** array | the assertion that failed must actually be recorded. v1.0 said "every entry", which an empty list satisfies vacuously; the non-empty requirement is now stated rather than inferred (C11) |
| the `FAIL` count **equals** the number of entries in `tests.not_ok` | the two halves of one record must agree (C11) |
| `tests.ran` is an integer not less than the number of failing targets | the count of tests run cannot be smaller than the failures within it (C11) |
| `tests.reconciled`, where present, is `true` | **an explicit `reconciled: false` refuses (C11)**: the producer has said its own accounting did not reconcile |
| every entry of `tests.not_ok` has `result == "FAIL"` and an `exception` beginning `AssertionError` | **the target failed *by assertion***, not by an unrelated error. A test that accepts any refusal would still pass, and a target that errored proves nothing |

**These per-control types are checked at E21, not at §2b (C12).** A wrong type here is a named refusal
under E21's template, not §2b's.

**Producer fields this decision consumes, and no others (C11).** `mutation_applied`, `tests.exit`,
`tests.by_outcome`, `tests.not_ok`, `tests.reconciled`, `tests.ran`. **Not consumed:** `verdict`,
`accounting_violations`, `collection`, `isolation`, and the database receipts. **This decision does not
recreate the producer's result parser and must not grow into one.**

**The set observed at the time of writing is two** — `MUT-fu-b2-unrelated-reuse-refusal` and
`MUT-fu-b3-unrelated-circle-check`. **Recorded as information, not as a check**: the registry grows as
packets add mutations, and pinning a count here would make every such packet require a contract
revision.

**The consumer records the derived expected set and the observed set in its decision record**, so a
reader sees what completeness was measured against.

## 4. The Copilot CI task's local predicates — accounted for one by one (C9)

C7 removed the separate PowerShell **decision engine**. It did not remove the Copilot CI task, which
still runs, and it must not silently remove the predicates that task applied. Each is accounted for
below.

### 4.1 Moved into the shared decision, because they are surface-independent

| Former local predicate | Now |
|---|---|
| `summary.cleanup.outcome == 'CLEAN'` | **E22** |
| `identity.commit_tree` and `identity.working_tree_git_tree` bound to the candidate tree | **E23** |
| exactly one `LOCK` step with outcome `PASS` | **E20**, generalised to all six fixed controls |
| `summary.result == 'PASS'`, `summary.exit_status == 0` | **E18** |
| the exit taken from `SURFACE_RESULT.final_exit` | **E1** |
| **`identity.valid == true`** | **E24 (new)** — `the coordinator summary records an invalid identity: <value>` |
| **`summary.route` names the route this decision is written against** | **E25 (new)** — `the evidence is from <found>, not the route this decision is written against (<expected>)`. The expected value is `S015 verification route v0.5` (RD-5, confirmed). It is exact: when the route's version changes, this statement changes with it, so that a consumer cannot silently accept evidence from a route it was not written for |

E24 and E25 were enforced by one consumer only. Had they stayed there, C7 would have removed them.

### 4.2 Retained as LOCAL-TASK predicates, because they are about the Windows surface

These stay in the Copilot CI task, in PowerShell. **They are checks, not a decision.**

- `environment.platform -like 'Windows*'`
- `NATIVE_COMMAND_CONTROLS.json` at R with `controls_as_declared == true`, `ps_edition == 'Desktop'`,
  `ps_version -like '5.1.*'` — written by the launcher, about the launcher's own host
- `identity.working_tree_git_tree`, `identity.commit_tree` and `LAUNCH_ATTESTATION.tree` all equal to
  the launcher's `NEW_TREE` constant — binds the evidence to the package the task itself built
- the run-folder search (`INTEVIA_UFUND2_RUNS/CHC_*`, newest first, tree match); a folder is a
  candidate only when all five records of §1 exist

**How the results combine.** The task's outcome is the **conjunction**: it applies its local
predicates, invokes the Python consumer over the same run root, and reads that consumer's decision
record. **The task reports qualifying only if its own predicates hold AND the consumer's decision is
qualifying.** A failure of either is not qualifying, and each is reported with its own reasons so a
reader can tell which held and which did not. The task never re-implements sections 1-3.

### 4.3 Owed, and recorded rather than dropped

- **`MANIFEST.sha256` verified over the evidence directory.** Surface-independent in principle and a
  candidate for section 3, but this seat has not read the manifest's format and will not specify one
  it has not seen. It **remains a local-task predicate** until the format is confirmed, at which point
  it moves to section 3 as a numbered check. Recorded so that its absence from the shared decision is
  visible rather than assumed.

### 4.4 The other two consumers

**`bootstrap.gate`** — **not a decision implementation under C10.** It stays exactly as it is: its
in-run checks, finalisation and failure propagation are preserved and **not** refactored to remove
duplication with §1-§3. It sets `final_exit`; it does not qualify a run. **No `--decide` entry is
required and none is to be added.** Its exit of 0 is a precondition the gate then tests at E1, never a
qualification.

**Launcher** — does not decide qualification. It extracts and blob-verifies `verification/bootstrap.py`
against `$BOOTSTRAP_BLOB` (a mismatch STOPS, exit 2, the route not run); starts the bootstrap under
`-I -S` with cwd = R passing `--checkout`, `--commit`, `--expected-tree`, `--run-root R`,
`--evidence-dir R/evidence`, `--run-id`; takes its own exit from the bootstrap's; and writes nothing
into R except `NATIVE_COMMAND_CONTROLS.json` and its transcript. `--skip-mutations` produces a run
that cannot qualify under E21: it is for diagnosis, never for a qualifying run.

**CI workflow decision step** — runs the same bootstrap from the **candidate commit's** blob; retains R
whole in the artifact including the decision record; reads `SURFACE_RESULT.final_exit` for the exit
mapping; and applies sections 1-3 with the same named refusals. A green check requires the
conjunction, not the exit alone. On a `pull_request` event `GITHUB_SHA` is a synthetic merge commit,
so the workflow binds the PR head commit for checkout, extraction, `--commit`, `--expected-tree` and
the evidence, and records the base SHA alongside. Human Governor ruling, 18 September 2026, option
(b): a scoped choice for clear candidate binding, **not** a judgement that merge-result testing is
invalid.

## 5. Evidence sets each consumer's own tests must discriminate (AM-26, extended)

**Sets 1–10 are v0.5's, unchanged.** Sets 11–16 are added for the new checks.

| # | Evidence set | Required decision |
|---|---|---|
| 1 | clean qualifying run (K composed c01; A1 `clean_positive`) | **qualifying** |
| 2 | snapshot changed after the coordinator sealed PASS/0 (c02, A1 `post_q6`) | refused: E1, E3 |
| 3 | final witness absent (c03) | refused: §1 |
| 4 | direct non-qualifying run (c06) | refused: §1 |
| 5 | direct run with summary edited to `snapshot-entry`/PASS and manifest regenerated (c07) | refused: §1 |
| 6 | `LAUNCH_ATTESTATION.run_id` removed (A1 `missing_attestation_run_id`) | refused: §2, naming `run_id` and the attestation |
| 7 | foreign `run_id`/`launch_token`/`commit` in the witness (A1 `foreign_witness_binding_fields`) | refused: E4, E5, E6, E9 |
| 8 | witness copied from a second real clean launch (A1 `witness_from_different_launch`) | refused: E4, E5, E9 |
| 9 | committed module imports a W-only helper that prints then raises a caught `ImportError` (A1 `w_import_failed`) | refused: E1, E2, E17 |
| 10 | the same helper importing successfully (A1 `w_import_success_negative`) | refused: E1, E2, E17 |
| **11 (C4)** | a real run started with `--skip-mutations`: every v0.5 check passes, **no** `MUT-` step present | refused: E21, naming **both** missing expected ids |
| **12 (C4)** | a qualifying run with **one of the two** mutation steps removed and the summary re-sealed | refused: E21, naming the one missing id — and **accepting is a failure of the set** |
| **13 (C4)** | a qualifying run with one fixed control (`LOCK`) removed, re-sealed | refused: E20, naming `LOCK` |
| **14 (C4)** | a qualifying run with one control duplicated (`S015` twice), re-sealed | refused: §2 rule (e), naming the repeated id |
| **15 (C4)** | a mutation step present and `outcome PASS`, but its target recorded `ERROR` rather than `FAIL` | refused: E21, naming the control and what was found |
| **16 (C5)** | `identity.commit_tree` altered, re-sealed | refused: E23 |
| **A1-01..03 (C11)** | `SOURCE_AUDIT.refusals` is `false`, `0`, `{}` | refused at §2b, naming the field and both types |
| **A1-04 (C11)** | `site_prequalification.refusals` is `false` | refused at §2b |
| **A1-05..06 (C11)** | `final_exit` is `false`, `0.0` | refused at §2b |
| **A1-07..08 (C11)** | a mutation records a `FAIL` count of `0`, `null` | refused at E21 |
| **A1-09 (C11)** | a mutation records `reconciled: false` | refused at E21 |
| **A1-10 (C11)** | `SURFACE_RESULT.json` carries `final_exit` twice | refused at §2 rule (f) |
| **A1-11..12 (C11)** | two inventory rows for the registry, either order | refused at §3.3(b) |
| **1 (again)** | **the same complete, valid evidence, unmodified** | **qualifying — no refusals** |

Sets 11–16 are file-level manipulations of a retained run root and the **re-seal technique** applies to
all: satisfy the seal so that only the binding can carry a refusal. That technique already exposed a
divergence between two implementations, which is why it is mandated rather than suggested.

**Every set is exercised against the ACTUAL authoritative gate**, on both its invocation paths — command
line and direct call — and never against a reference model. **The clean positive must be preserved**: a
change that refuses the negatives by refusing everything has discharged nothing. The surface-specific
predicates of §4.2 apply on the path that imposes them.

**Sets 8, 9 and 10 are retired from this candidate's completion criteria (C10).** They exist to compare
a second genuine launch and a W-only-helper run **across implementations**, and there is one
implementation. They carry to PKT-B as follow-on work and no longer make a conformance run incomplete
here.

**Plus the malformed-evidence control:** missing, null, contradictory or malformed evidence must be a
named refusal and a non-qualifying outcome, never an unhandled error and never green. Demonstrated for
`s015_gate.py` on 18 September 2026 against an empty run root and against malformed JSON in all five
records — five named refusals and exit 1 in both cases, re-demonstrated on the Human Governor's machine
before the gate was committed. **Demonstrated against the authoritative gate on both invocation paths.**

---

## 6. The coordinated candidate correction

### 6.1 One candidate, five members

This statement is not adopted on its own. It is prepared and returned with the implementations it
governs, so that the contract and its consumers move together and their conformance expectations move
with them:

| Member | State |
|---|---|
| `CONSUMER_REQUIREMENT_v1_0.md` (this statement) | prepared |
| `.github/s015_gate.py` — the authoritative decision | prepared, 37 controls exercised |
| `gate_conformance.py` — the controls of §5 against that gate | prepared and exercised |

`bootstrap.gate` is not a member: C10 preserves it unchanged. Work produced against the withdrawn
`--decide` commission is **preserved as a record and not adopted**; the two files it modified are
reverted, so the bootstrap's safety path is the one qualified on 18 September 2026.

**Control inventory, reconciled.** 40 cases: **37 constructible here** and **3 executed elsewhere**.
Sets 1-7 and 11-17 are the contract's own; 8, 9 and 10 need a real run (§5). P1-P6 are the advisory
probes; R1-R3 the registry derivation; S1-S8 the site-list shapes; T1-T3 the empty-list handling;
V1-V2 the two predicates that moved in under C9. **37 are exercised against the authoritative gate on
both invocation paths; sets 8-10 are retired to PKT-B under C10.**

The separate PowerShell decision engine was withdrawn by C7; the second decision implementation by
C10. All attempts are preserved as records and none is withdrawn. The Copilot CI task continues, with
the local predicates of §4.2 and the conjunction rule of §4.2.

**No interim wording revert is performed.** v0.6 proposed reverting `s015_gate.py` and changing it back on
adoption; that is withdrawn as churn on a landed object. The divergence is disclosed instead (§6.2).

Every member is written **against this statement** and **records its dependency on the contract's
approval explicitly**: none is adopted, and none can be, until the statement is.

The whole delta and its control results are returned through **A1's scheduled focused check**.

### 6.2 The current wording divergence, preserved and disclosed

`.github/s015_gate.py` as landed at `7622cce` and later says **`launch attestation`** in its §1 and §2
refusal text. `CONSUMER_REQUIREMENT_v0_5.md`, which governs until this statement is adopted, specifies
**`launcher attestation`** verbatim. `gate_conformance.py` line 89 asserts `"launcher attestation" in r`
for evidence set 6 and would therefore fail against the gate as landed.

**Precise limits of the earlier qualification results.**

- Both qualifying runs of 18 September 2026 were decided against **v0.5 §1–§3 only**.
- **Neither run produced any refusal.** No refusal text was exercised in either, so the divergence above
  did not affect either verdict, and no qualification rests on either wording.
- **Neither run's evidence was examined for completeness of controls, cleanup, or tree binding**, because
  those checks did not exist. E20, E21 and E22 were verified after the fact against the local run's real
  `summary.json` and **accepted it with no refusals**; E23 was **not** verified, because the local
  `LAUNCH_ATTESTATION.json` was not held by the seat. Nothing in this paragraph is a qualification.
- A clean positive demonstrates **acceptance only, never refusal**. Neither run discharges any evidence
  set of §5.

### 6.2b Provenance must be independent of the run it attests

A provenance record binds a retained run root to the qualified source artefact it came from. **It must
come from a source independent of that run.** A record whose `run_id`, `commit` and `tree` are copied
out of the run's own launch attestation attests nothing: it agrees with the attestation because it was
made from it, and a reader learns only that copying works.

Recorded because it happened: on 18 September 2026 a provenance record was generated from the
attestation's own fields and a conformance run then reported the evidence **RETAINED** on that basis.
**That classification is not relied upon.** Acceptable sources are the launcher transcript and package
identity that produced the run, or a CI run and artifact identity — something written outside the run
root by a different act.

### 6.3 Reporting requirements — distinct from E23

These are requirements on **reports**, not equality checks on evidence. They are stated here because §3.2
separates them from E23, which speaks only to the integrity of the tree across one run's records.

1. **Resolvability of a named commit.** A CI run names a commit that is on the branch and resolvable by
   any third party. The Windows launcher builds its candidate in an isolated clone and makes a local
   commit whose id is a deterministic function of the tree, the base commit and a fixed message; that
   commit is never pushed and exists in no repository. **A report that cites a launcher-determined commit
   must say that it is local to the run and state the base commit and the tree by which it can be
   reconstructed.**
2. **Completeness of qualification reporting.** A report must state which controls ran, the expected set
   they were measured against and where that set was derived from, the surface, the environment, and the
   exact records the decision consumed. **"Qualifying" without that account is not a qualification
   report.**
3. **Two surfaces, one tree.** A report presenting both qualifications must state the **tree** as the
   quantity they share, give each surface's commit separately, and **must not state or imply that the
   same commit ran in both**.

### 6.4 What adoption requires

1. E19–E23 require **no change to the producers**: `steps`, `cleanup`, `route`, `attested_commit`,
   `commit_tree`, `working_tree_git_tree`, `tree`, `mutation_applied` and the per-control test evidence
   are all present in the records of both qualifying runs of 18 September 2026. **This statement requires
   them; it does not add them.**
2. The five members of §6.1 are adopted or refused together.
3. The consumers are re-run against all sixteen evidence sets, against the **actual** consumers.

---

## 7. Authority boundary

Design statement only. It authorises nothing, implements nothing, promotes nothing and accepts nothing. It
does not amend, supersede, promote or activate any protocol version.

**Contract approval is separate from HAT/IDOP amendment adoption and from repository landing.** Approving
this statement adopts no amendment and lands nothing.

Existing review-branch authorisations continue. **No merge and no write to `main` is authorised.**

CI qualification is not local qualification. Neither is implementation acceptance. **A passing check is
evidence for the tested tree and environment: it is not review, landing, external reproduction or Human
acceptance.**

Final authority remains with Carmian Owen, Human Governor.
